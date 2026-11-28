"""MainWindow pages mixin (part): views pages — Calendar, Inbox, Contacts, Calls.

UI Phase 3 — Productivity (2026-11-25): the four read-only Stage 0
views are now usable workspaces. Each advertised primary job passes
end-to-end interaction tests (blueprint §09 exit gate).

- Calendar: Month / Week / Agenda views over the real events, an
  event detail drawer, and create / edit (create-new + cancel-old)
  / reschedule / cancel by event id — every write confirm-gated
  through the Phase 1 approval contract (60 s, safe default Don't
  allow, no always-allow) with a receipt after it lands.
- Inbox: triage tabs (All / Unread / Snoozed), search (Top 6
  stated), a reading pane with a labelled Summary line and
  attachment listing, compose / reply in one drawer with a local
  in-memory draft ("Draft — not saved") and pre-send review of the
  FINAL payload, snooze through the local store.
- Contacts: a card grid (initials discs, never photos), a detail
  drawer with per-detail Copy, create / edit (save-new then
  delete-old) / delete (exact-key store function, confirm-gated),
  and related calls linked by exact phone match.
- Calls: a timeline of cards (latest 20, Load more), a detail
  drawer with the recorded agent-flow transcript, search / filter,
  and a follow-up that prefills a reminder with explicit
  confirmation.

What Phase 3 does NOT build (the dependency gates proved the seam
missing, so the surface is absent, not faked): suggested replies
(no reply-generation seam), email-to-calendar extraction (no event
extraction seam), a Contacts Call button (no GUI prefill seam for
the voice-call path — the phone detail gets a Copy action),
labels / archive / folders (no seam), recurrence (create_event has
no recurrence parameter), a missed-call filter or count (the
outcome vocabulary holds only "completed" and "answered").
The Week now-line runs on a page-scoped 60 s timer (the
window-wide 1 s vitals tick the plan assumed does not exist in a
connectable form — flagged for the parent to rule).

Stack indices are FROZEN: Calendar 8, Inbox 9, Contacts 10,
Calls 11. The sidebar section is WORKSPACES (D-P3-12). Writes go
through the facade's google_services / skills_admin_schedules /
voice_agent / scheduler entries — the GUI never imports core
directly. Private mode blanks content per page and hides the
write buttons (D-P3-10); counts stay.
"""

import calendar as _calendar
import datetime
import html
import os
import re

from PySide6.QtCore import QDate, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence, QShortcut, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .design_system.drawer import ContextDrawer
from .design_system.empty import EmptyState
from .design_system.pills import StatusPill
from .design_system.tabs import HudTabs
from .style import (
    ACCENT,
    LINE_STRONG,
    LINE_SUBTLE,
    RADIUS_CARD,
    SURFACE_1,
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

# Inbox list cap (recent_messages(20)) and the unread-search cap
# (search_email("is:unread") is the only per-message unread seam —
# recent_messages carries no read flag — so the Unread tab and the
# All-row markers read from it; the cap is stated on the page).
_INBOX_LIST_LIMIT = 20
_UNREAD_SEARCH_LIMIT = 50
_SEARCH_LIMIT = 6
_CALLS_PAGE_SIZE = 20
_MAX_CONTACT_NAME_CHARS = 80
_MAX_CONTACT_DETAIL_CHARS = 200
# The event-row ink placeholder: the row builder wraps the event
# line in <span style="color:__VYUK_ROW_INK__"> and the rich-text
# delegate substitutes the paint-time ink (themed text, or dark on a
# selected row). The colour MUST ride inline — the document's
# default style sheet ("body { color: ... }") does NOT colour the
# text in this Qt build (proven by variant probe /tmp/probe_rich3.py:
# body-sheet, p-selector and no-sheet all render nothing bright,
# while an inline span renders 307 solid pixels at full brightness),
# so the body's default colour left the event line faint grey while
# the conflict span (inline #B0C1CD) read bright — the hierarchy
# inverted. The token is long and distinctive so no real event title
# (already html-escaped) can contain it.
_VIEWS_ROW_INK = "__VYUK_ROW_INK__"
# Attachment ceiling, mirroring the backend (5 files, 25 MB each) —
# enforced in the compose drawer before confirm, never only at send.
_ATTACH_MAX_FILES = 5
_ATTACH_MAX_BYTES = 25 * 1024 * 1024
# The attachment-listing probe: download_attachment with a filename
# that matches nothing returns the listing ("it has 'a' (12 KB) …")
# and downloads nothing. The token is long and distinctive so no
# real attachment name can contain it; the listing call is a pure
# metadata read, never a download.
_ATTACH_LISTING_PROBE = "__VYUK_ATTACHMENT_LISTING_PROBE__"


def _outcome_severity(outcome: str) -> str:
    """The honest StatusPill severity for a recorded call outcome.

    Phase 0: the pill names the outcome in words (the raw text is
    the pill's text, never paraphrased) — the severity only adds
    the glyph. Answered/completed are done, failed/error are
    faults, missed/no-answer/cancelled are warnings; anything the
    ledger holds that this does not know is neutral, not guessed.
    """
    value = (outcome or "").strip().lower()
    if value in ("answered", "completed"):
        return "success"
    if value in ("failed", "error"):
        return "danger"
    if value in ("missed", "no_answer", "cancelled", "abandoned"):
        return "warning"
    return "neutral"


_CONNECT_GOOGLE = (
    "Connect Google in Settings → Your world to see this here."
)


def _event_time(when: datetime.datetime) -> str:
    """"2:30 PM" — %I with the leading zero stripped (Windows-safe)."""
    return when.strftime("%I:%M %p").lstrip("0")


def _views_call_when(started, ended) -> str:
    """Epoch stamps as "Tue 25 Nov, 10:15 AM – 10:25 AM" — the raw
    floats never reach the screen (a 1280 render caught them).
    Same-day ranges show the date once; anything unparseable
    reads as "" so the meta falls back to the direction alone.
    """
    def _fmt(value):
        try:
            return datetime.datetime.fromtimestamp(float(value))
        except (TypeError, ValueError, OverflowError, OSError):
            pass
        # The store also holds ISO strings (tests seed them that
        # way); parse those too rather than dropping the time.
        try:
            return datetime.datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    start = _fmt(started)
    end = _fmt(ended)
    if start is None and end is None:
        return ""
    if start is not None and end is not None:
        if start.date() == end.date():
            return (f"{start.strftime('%a %d %b')}, "
                    f"{_event_time(start)} – {_event_time(end)}")
        return (f"{start.strftime('%a %d %b')}, {_event_time(start)} – "
                f"{end.strftime('%a %d %b')}, {_event_time(end)}")
    only = start if start is not None else end
    return f"{only.strftime('%a %d %b')}, {_event_time(only)}"


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


def _views_parse_search_results(text: str) -> list[dict]:
    """Parse search_email / unread prose into message rows.

    The backend renders one result as a bullet line
    "• {sender} — {subject} ({when}) [id: {id}]" with the snippet
    on the following line. The id marker is the documented way the
    brain names a message later (#25) — the parse is exact, never
    fuzzy: a bullet without an [id: …] marker yields no row, and
    a snippet line attaches to the bullet above it.
    """
    rows: list[dict] = []
    current = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line.startswith("• "):
            match = re.search(r"\[id: ([^\]]+)\]\s*$", line)
            if not match:
                current = None
                continue
            head = line[2:match.start()].strip()
            when = ""
            when_match = re.search(r"\(([^)]*)\)\s*$", head)
            if when_match:
                when = when_match.group(1)
                head = head[:when_match.start()].strip()
            if " — " in head:
                sender, subject = head.split(" — ", 1)
            else:
                sender, subject = head, ""
            current = {"id": match.group(1).strip(),
                       "sender": sender.strip(),
                       "subject": subject.strip() or "(no subject)",
                       "when": when, "snippet": ""}
            rows.append(current)
        elif current is not None and line:
            current["snippet"] = line
            current = None
    return rows


def _views_parse_free_blocks(text: str) -> list[tuple[str, str]]:
    """Parse find_free_slots prose into (start, end) HH:MM pairs.

    The backend renders free blocks as "08:00–20:00" joined by
    ", " — a pair that does not parse yields no button, never a
    guessed slot.
    """
    return [(m.group(1), m.group(2)) for m in re.finditer(
        r"(\d{2}:\d{2})–(\d{2}:\d{2})", text or "")]


def _views_initials(name: str) -> str:
    """Initials for a contact disc — first letters of the first
    two words; a name with no usable initials still renders (the
    first character, or "?" — P3-18). Never a photo, never a
    generated face."""
    words = [w for w in (name or "").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][0].upper()
    return (words[0][0] + words[1][0]).upper()


def _views_sender_address(sender: str) -> str:
    """The email address a sender string carries, or "".

    recent_messages strips addresses to display names — the only
    matchable case is a bare address ("priya@example.com"). A
    display name is never treated as an address.
    """
    from email.utils import parseaddr
    _name, addr = parseaddr(sender or "")
    if addr and (sender or "").strip().lower() == addr.lower():
        return addr
    return ""


def _views_sender_name(sender: str) -> str:
    """The name a sender string shows — never a bare address.

    A display name is used as-is ("Priya Nair", or the name part
    of "Ada Lovelace <ada@example.test>"). A bare address shows
    its local part as a name ("ada@example.test" → "Ada"):
    the list used to print the raw address as the sender."""
    from email.utils import parseaddr
    name, addr = parseaddr(sender or "")
    if name:
        return name
    # parseaddr takes only the first word of a bare display name
    # ("Priya Nair" -> addr "Priya"), so a bare address is
    # recognised only when the WHOLE string is the address.
    if (addr and "@" in addr
            and (sender or "").strip().lower() == addr.lower()):
        local = addr.split("@", 1)[0]
        return " ".join(
            w.capitalize()
            for w in re.split(r"[._\-]+", local) if w) or sender
    return sender or "Unknown sender"


def _views_elide(text: str, limit: int) -> str:
    """Shorten to at most `limit` characters at a word boundary,
    with an ellipsis — a list row never wraps mid-sentence and
    never cuts a word in half."""
    text = text or ""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut + "…"


class _ViewsMonthDelegate(QStyledItemDelegate):
    """Month-grid cells: day number + event titles + event dots.

    The dots ARE the count marking (P3-22) and the titles are the
    event text inside the cell — a day with events reads as one at
    a glance, and the selected day stays visibly marked (its dots
    and titles are drawn in dark ink on the accent fill, never
    accent-on-accent, which used to hide them). The delegate only
    paints, it never handles keys (P3-09). Titles are elided to
    the cell width; as many title lines are painted as the row
    height fits (one at the usual height, two when the rows grow).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = QColor(TEXT)
        self._muted = QColor(TEXT_MUTED)
        self._accent = QColor(ACCENT)
        self._selected_ink = QColor("#06222B")

    def set_theme(self, resolved: dict) -> None:
        try:
            self._text = QColor(resolved.get("text", TEXT))
            self._muted = QColor(resolved.get("text_muted", TEXT_MUTED))
            self._accent = QColor(resolved.get("accent", ACCENT))
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass

    def paint(self, painter, option, index):
        from PySide6.QtGui import QPainter
        day = index.data(Qt.ItemDataRole.DisplayRole)
        count = index.data(Qt.ItemDataRole.UserRole + 1) or 0
        other_month = bool(index.data(Qt.ItemDataRole.UserRole + 2))
        titles = index.data(Qt.ItemDataRole.UserRole + 3) or []
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        try:
            if selected:
                painter.fillRect(option.rect, self._accent)
                ink = self._selected_ink
            elif other_month:
                ink = self._muted
            else:
                ink = self._text
            painter.setPen(ink)
            # The table's face, not the painter's: a bare painter
            # carries the app default ("Sans Serif" 9 pt), whose
            # stepped-down face is 15 px high and leaves no room
            # for a title line at 1280 — the budget then computed
            # zero lines while the data was right. option.font is
            # the table's face in both the view and a direct paint.
            font = option.font
            font.setBold(False)
            painter.setFont(font)
            rect = option.rect
            painter.drawText(rect.adjusted(6, 2, 0, 0),
                             Qt.AlignmentFlag.AlignLeft
                             | Qt.AlignmentFlag.AlignTop, str(day or ""))
            # Event titles between the number and the dots: a
            # smaller face, elided to the cell, as many lines as
            # the row height fits. The dots below still carry the
            # count, so a cell that fits one title of three still
            # says how many events the day holds.
            num_h = QFontMetrics(font).height()
            small = QFont(font)
            if small.pointSize() > 0:
                small.setPointSize(max(8, small.pointSize() - 3))
            elif small.pixelSize() > 0:
                small.setPixelSize(max(9, small.pixelSize() - 3))
            title_fm = QFontMetrics(small)
            dots_h = title_fm.height()
            # The vertical budget is tight on purpose: at the
            # usual 1280 row height (~51 px) the old budget
            # (2 px top + number + 1 px gap + dots + 2 px bottom)
            # left 14 px for a 15 px title line, so NO title ever
            # painted and the cells showed dots only. One px off
            # the top, the gap and the bottom buys the first
            # title line back; taller rows still stack more.
            avail = rect.height() - 1 - num_h - dots_h - 1
            lines = max(0, min(len(titles),
                               avail // max(1, title_fm.height())))
            if lines:
                painter.setFont(small)
                y = 1 + num_h
                for title in titles[:lines]:
                    shown = title_fm.elidedText(
                        str(title), Qt.TextElideMode.ElideRight,
                        max(8, rect.width() - 12))
                    painter.drawText(
                        rect.adjusted(6, y, -4, 0),
                        Qt.AlignmentFlag.AlignLeft
                        | Qt.AlignmentFlag.AlignTop, shown)
                    y += title_fm.height()
                painter.setFont(font)
            if count:
                dots = "●" * min(int(count), 3)
                if int(count) > 3:
                    dots += f" +{int(count) - 3}"
                painter.setPen(ink if selected else self._accent)
                painter.setFont(small)
                painter.drawText(rect.adjusted(6, 0, -4, -2),
                                 Qt.AlignmentFlag.AlignLeft
                                 | Qt.AlignmentFlag.AlignBottom, dots)
        finally:
            painter.restore()


class _ViewsWeekDelegate(QStyledItemDelegate):
    """Week-grid cells: events as filled chips.

    A cell that carries an event id paints as a filled accent
    chip with dark ink, so events read as events instead of bare
    text in a spreadsheet; overlapping events stack as lines of
    the one chip (the fill groups them). Cells without an event
    id — the hour gutter, empty cells, the offline line — paint
    as usual. Today's column gets a faint accent tint so the
    grid has an anchor even when it holds few events.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = QColor(TEXT)
        self._accent = QColor(ACCENT)
        self._ink = QColor("#06222B")
        self._today_col = -1
        self._today_tint = QColor(ACCENT)
        self._today_tint.setAlpha(20)

    def set_theme(self, resolved: dict) -> None:
        try:
            self._text = QColor(resolved.get("text", TEXT))
            self._accent = QColor(resolved.get("accent", ACCENT))
            self._ink = QColor("#06222B")
            self._today_tint = QColor(self._accent)
            self._today_tint.setAlpha(20)
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass

    def set_today_column(self, col: int) -> None:
        self._today_col = col

    def paint(self, painter, option, index):
        from PySide6.QtGui import QPainter
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        event_id = index.data(Qt.ItemDataRole.UserRole) or ""
        if not text or not event_id:
            if index.column() == self._today_col \
                    and self._today_col > 0:
                painter.save()
                try:
                    painter.fillRect(option.rect, self._today_tint)
                finally:
                    painter.restore()
            super().paint(painter, option, index)
            return
        painter.save()
        try:
            chip = option.rect.adjusted(2, 2, -2, -2)
            painter.setRenderHint(
                QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._accent)
            painter.drawRoundedRect(chip, 6, 6)
            if option.state & QStyle.StateFlag.State_Selected:
                painter.setPen(QColor(self._text))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(chip, 6, 6)
            painter.setPen(self._ink)
            # The table's face, not the painter's (same trap as
            # the month delegate: a bare painter carries the app
            # default). Three points down, not two: at 11 px the
            # longest chip line ("9:00 AM Tomorrow sync", 135 px)
            # still elided against the 128 px text width; at 10 px
            # it measures 123 px and fits whole.
            font = option.font
            font.setBold(False)
            if font.pointSize() > 0:
                font.setPointSize(max(8, font.pointSize() - 3))
            elif font.pixelSize() > 0:
                font.setPixelSize(max(9, font.pixelSize() - 3))
            painter.setFont(font)
            fm = QFontMetrics(font)
            rect = option.rect
            for pos, line in enumerate(str(text).split("\n")):
                shown = fm.elidedText(
                    line, Qt.TextElideMode.ElideRight,
                    max(8, chip.width() - 8))
                painter.drawText(
                    rect.adjusted(8, 6 + pos * fm.height(), -8, 0),
                    Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignTop, shown)
        finally:
            painter.restore()


class _ViewsRichTextDelegate(QStyledItemDelegate):
    """List rows that carry markup: a QListWidgetItem shows its
    text as plain text, so a row built as bold-time plus a muted
    conflict line painted its tags on screen ("<b>10:00 AM…")
    instead of the rich row. This delegate draws the item's
    markup through a text document, over the usual selection
    background. Only the two calendar lists use it — the Inbox
    rows are plain text and need no document."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = QColor(TEXT)

    def set_theme(self, resolved: dict) -> None:
        try:
            self._text = QColor(resolved.get("text", TEXT))
        except Exception:  # noqa: BLE001 — theme never breaks paint
            pass

    def paint(self, painter, option, index):
        from PySide6.QtGui import QTextDocument
        from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # The background (selection, hover, focus) without the
        # plain text: the document below draws the markup.
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None \
            else QApplication.style()
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        # The event line always takes the themed text — never the
        # dark ink the table delegates use on selection. A table
        # cell's selection background is a solid accent fill, so
        # dark ink reads there; a list row's selection background
        # is only a faint accent tint (rgba(53, 214, 255, 52) —
        # the selected row measured #0b2c34 over black, every
        # top-band pixel dark), so dark ink on a selected row is
        # dark-on-dark and the event line vanishes. The tint is
        # dark in both themes, so the light text stays readable.
        ink = self._text
        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        # The event line's ink rides inline (see _VIEWS_ROW_INK):
        # the body's default stylesheet does NOT colour the text
        # in this Qt build — the line rendered faint grey while
        # the conflict span (inline #B0C1CD) read bright — so the
        # paint-time ink is substituted into the row markup here.
        # Selected rows take the dark ink, like the month cells.
        _html = (index.data(Qt.ItemDataRole.DisplayRole) or "")
        doc.setHtml(_html.replace(_VIEWS_ROW_INK, ink.name()))
        doc.setTextWidth(max(8, opt.rect.width() - 8))
        # The document sits centred in its row: the Agenda rows are
        # tall (76 px) for two lines of text, and top-aligned text
        # in a tall row reads as a thin line over a void. Month rows
        # are barely taller than the document, so centring is a
        # no-op there.
        _doc_h = doc.size().height()
        _y = opt.rect.y() + max(
            2, (opt.rect.height() - int(_doc_h)) // 2)
        painter.save()
        try:
            painter.translate(opt.rect.x() + 4, _y)
            doc.drawContents(painter)
        finally:
            painter.restore()

    def sizeHint(self, option, index):
        from PySide6.QtGui import QTextDocument
        hint = index.data(Qt.ItemDataRole.SizeHintRole)
        if hint is not None and isinstance(hint, QSize) \
                and hint.isValid():
            return hint
        doc = QTextDocument()
        doc.setDefaultFont(option.font)
        _html = (index.data(Qt.ItemDataRole.DisplayRole) or "")
        doc.setHtml(_html.replace(_VIEWS_ROW_INK,
                                  self._text.name()))
        width = 300
        widget = option.widget
        if widget is not None:
            try:
                width = max(80, widget.viewport().width() - 8)
            except Exception:  # noqa: BLE001 — fall back to 300
                pass
        doc.setTextWidth(width)
        return QSize(int(doc.idealWidth()) + 8,
                     int(doc.size().height()) + 4)


class _ViewsNowTag(QLabel):
    """The Week now-line's tag: it paints its own accent fill and
    dark text in paintEvent. A palette plus autoFillBackground did
    NOT hold — in the 1280 dark Week render the tag read as a plain
    white box over the hour label (the background came out as the
    palette's Base, white, not its Window, accent), while the same
    code in the test harness painted nothing visible at all.
    paintEvent is deterministic in both: the fill and the ink are
    set on the painter, never read back from a palette."""

    def __init__(self, parent=None):
        super().__init__("Now", parent)
        _font = self.font()
        _font.setBold(True)
        self.setFont(_font)

    def paintEvent(self, _event):  # noqa: N802 — Qt override
        from PySide6.QtGui import QPainter
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(ACCENT))
        painter.setPen(QColor("#06222B"))
        painter.setFont(self.font())
        painter.drawText(self.rect(),
                         Qt.AlignmentFlag.AlignCenter, "Now")
        painter.end()


class _ViewsSnoozeDialog(QDialog):
    """The snooze picker: exactly the durations the backend
    parses — Tomorrow 09:00, a date (09:00 that day), a date+time.
    Nothing else exists. A past time is refused in the picker
    with the reason, not silently rounded."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Snooze until")
        self.until = ""
        self._refusal = QLabel("")
        self._refusal.setObjectName("muted")
        self._refusal.setWordWrap(True)
        self._choice = QComboBox()
        self._choice.addItems(["Tomorrow 09:00", "Pick a date",
                               "Pick a date and time"])
        self._date = QDateEdit()
        self._date.setDate(QDate.currentDate().addDays(1))
        self._date.setCalendarPopup(True)
        self._datetime = QDateTimeEdit()
        self._datetime.setDateTime(
            datetime.datetime.now() + datetime.timedelta(days=1))
        self._datetime.setCalendarPopup(True)
        for widget in (self._date, self._datetime):
            widget.setEnabled(False)
        self._choice.currentIndexChanged.connect(self._on_choice)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Snooze this message until:"))
        lay.addWidget(self._choice)
        lay.addWidget(self._date)
        lay.addWidget(self._datetime)
        lay.addWidget(self._refusal)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _on_choice(self, index):
        self._date.setEnabled(index == 1)
        self._datetime.setEnabled(index == 2)
        self._refusal.setText("")

    def _on_accept(self):
        from voice_assistant.core.gui_facade import google_services
        index = self._choice.currentIndex()
        if index == 0:
            until = "tomorrow"
        elif index == 1:
            until = self._date.date().toPython().isoformat()
        else:
            until = self._datetime.dateTime().toPython().isoformat()
        try:
            parsed = google_services._parse_snooze_until(until)
        except Exception:  # noqa: BLE001 — an unparsable value
            parsed = None  # is refused, never silently rounded.
        if parsed is None:
            self._refusal.setText(
                "I couldn't understand that time — say \"tomorrow\", "
                "a date like 2026-11-23, or an ISO time.")
            return
        if parsed <= datetime.datetime.now().astimezone():
            self._refusal.setText(
                "That time is already past — pick a future time.")
            return
        self.until = until
        self.accept()


class _ViewsCard(QFrame):
    """One clickable card (Contacts grid, Calls timeline).

    A frame with a chosen signal — click or Enter/Space opens it.
    The content is whatever the caller lays inside; the card only
    owns the affordance, never the data.
    """

    chosen = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setObjectName("viewsCard")
        self.set_theme({})

    def set_theme(self, resolved: dict) -> None:
        """Re-apply the card face from a resolved theme (fix round,
        2026-11-25): the constructor baked the dark house SURFACE_1,
        so on Light the cards stayed dark navy while their labels
        turned Light-dark — invisible. Dark keeps the house values
        exactly; Light takes the resolved surface."""
        try:
            surface_1 = resolved.get("surface_1", SURFACE_1)
            line = resolved.get("line_subtle", LINE_SUBTLE)
        except Exception:  # noqa: BLE001 — theme never breaks the card
            surface_1, line = SURFACE_1, LINE_SUBTLE
        self.setStyleSheet(
            f"#viewsCard {{ background: {surface_1}; "
            f"border: 1px solid {line}; "
            f"border-radius: {RADIUS_CARD}px; }}")

    def mousePressEvent(self, event):
        try:
            self.chosen.emit()
        except Exception:  # noqa: BLE001
            pass
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Space):
            try:
                self.chosen.emit()
            except Exception:  # noqa: BLE001
                pass
            return
        super().keyPressEvent(event)


class PagesViewsMixin:
    # ------------------------------------------------------------------
    # Shared helpers (all four workspaces)
    # ------------------------------------------------------------------
    def _views_private_on(self) -> bool:
        """Private mode, read live — never cached (D-P3-10)."""
        try:
            from voice_assistant.core.gui_facade import private_mode
            return bool(private_mode.private_mode_on())
        except Exception:  # noqa: BLE001 — a failed check reads
            return False  # as off; the pages still blank nothing.

    def _views_can_write(self, scope: str) -> bool:
        """True when the stored sign-in provably carries a write
        scope ("calendar" / "gmail").

        The exact check the write path itself performs
        (_resolve_write_service): load the credentials, read the
        scopes Google actually granted, compare. When the granted
        scopes cannot be determined (older token files) the
        backend's rule is "don't guess" — the write proceeds and
        its refusal is classified instead — so this returns True
        and the write attempt is the verdict. Any failure here
        also reads as True: a scope probe must never hide a
        control the backend would have allowed.
        """
        try:
            from voice_assistant.core.gui_facade import google_services
            creds = google_services._load_credentials()
            granted = google_services._granted_scopes(creds) \
                if creds is not None else None
            if granted is None:
                return True
            want = (google_services.CALENDAR_EVENTS_SCOPE
                    if scope == "calendar"
                    else google_services.GMAIL_SEND_SCOPE)
            return want in granted
        except Exception:  # noqa: BLE001
            return True

    def _views_scope_sentence(self, action: str) -> str:
        """The backend's own scope sentence, verbatim — never a
        paraphrase ("…needs one more permission — reconnect Google
        in Settings to grant it.")."""
        try:
            from voice_assistant.core.gui_facade import google_services
            return str(google_services._scope_missing_message(action))
        except Exception:  # noqa: BLE001
            return ("Google is connected for reading, but " + action +
                    " needs one more permission — reconnect Google "
                    "in Settings to grant it.")

    def _views_confirm_write(self, tool: str, label: str,
                             sentence: str) -> bool:
        """One explicit approval for a GUI-initiated write, through
        the Phase 1 confirm contract (D-P3-02): the shared S-1
        confirm dialog, 60 s countdown, safe default Don't allow,
        no always-allow, no voice path.

        The sentence carries the FINAL payload (S-P3-01): the
        dialog's Details section shows it verbatim for the whole
        countdown, and the drawer behind the dialog keeps the
        payload visible too — the card never replaces the drawer.
        A broken popup denies, never allows. Tests stub this
        method (True / False) so no dialog ever opens in a suite.
        """
        try:
            from .pending_approval import present_pending_dialog
            return bool(present_pending_dialog(
                self, {"tool": tool, "label": label,
                       "sentence": sentence,
                       "confirm_arg": "confirmed"}))
        except Exception:  # noqa: BLE001 — a broken popup denies.
            return False

    def _views_connect_button(self) -> QPushButton:
        """The not-connected state's Connect button (D-P3-13):
        Settings → Your world, landing on the exact card (P3-19),
        not just the Settings page."""
        btn = QPushButton("Connect Google")
        btn.setObjectName("primaryBtn")
        try:
            btn.clicked.connect(
                lambda: self.show_settings_card("Your world"))
        except Exception:  # noqa: BLE001 — the button still
            pass  # shows; the worst case is Settings itself.
        return btn

    def _views_drawer_width(self) -> int:
        """The shared drawer width budget (§7.1), from the live
        window width: 420 / 380 / 340 / 320 across the tiers."""
        try:
            width = int(self.width())
        except Exception:  # noqa: BLE001
            width = 1280
        if width >= 1600:
            return 420
        if width >= 1100:
            return 380
        if width >= 900:
            return 340
        return 320

    def _views_open_drawer(self, drawer, main) -> None:
        """Show a drawer. At 640 the drawer is a full-width
        overlay, not a squeezed side panel (§7.1): the list side
        hides while the drawer is open (a drill-in) and comes back
        on close.

        UI Phase 6 (§3.2): a drawer is a keyboard trap without an
        Escape close and a focus return, so opening records the
        invoking widget and installs (once per drawer) an Escape
        shortcut scoped to the drawer and its children — Escape
        closes the drawer from any control inside it, and the
        close returns focus to the opener.
        """
        try:
            drawer.setFixedWidth(self._views_drawer_width())
        except Exception:  # noqa: BLE001
            pass
        try:
            drawer._views_opener = QApplication.focusWidget()
            drawer._views_main = main
        except Exception:  # noqa: BLE001
            pass
        try:
            if not hasattr(drawer, "_views_escape_sc"):
                sc = QShortcut(QKeySequence("Escape"), drawer)
                sc.setContext(
                    Qt.ShortcutContext.WidgetWithChildrenShortcut)
                sc.activated.connect(
                    lambda: self._views_close_drawer(
                        drawer, getattr(drawer, "_views_main", None)))
                drawer._views_escape_sc = sc
        except Exception:  # noqa: BLE001
            pass
        drawer.show()
        try:
            if int(self.width()) < 700 and main is not None:
                main.hide()
        except Exception:  # noqa: BLE001
            pass

    def _views_close_drawer(self, drawer, main) -> None:
        """Close a drawer and restore the list side. Focus returns
        to the widget that opened the drawer (recorded at open
        time); a dead, hidden, or disabled opener is left alone,
        never crashed on."""
        try:
            drawer.hide()
        except Exception:  # noqa: BLE001
            pass
        try:
            if main is not None:
                main.show()
        except Exception:  # noqa: BLE001
            pass
        try:
            opener = getattr(drawer, "_views_opener", None)
            drawer._views_opener = None
        except Exception:  # noqa: BLE001
            opener = None
        if opener is not None:
            try:
                if opener.isVisible() and opener.isEnabled():
                    opener.setFocus()
            except Exception:  # noqa: BLE001 — dead opener
                pass

    def _views_toast(self, text: str) -> None:
        """A write receipt that never yanks the user off the page
        (_notify would jump to Home; the toast announces in place)."""
        try:
            show = getattr(self, "show_toast", None)
            if callable(show):
                show(text)
        except Exception:  # noqa: BLE001 — a receipt never
            pass  # breaks the page; the drawer shows it too.

    def _refresh_views_theme(self, resolved: dict) -> None:
        """Re-apply the Views panels from a theme (batch 5, Light).

        The month/week tables and the reading pane carry scoped
        sheets with the house tokens — the "dark pane" precedent
        (a bare widget renders with Qt's default light palette, so
        the sheet darkened it). On Light the house tokens left
        black voids with theme-dark text. The window's appearance
        pass re-runs this from the resolved theme; each build site
        runs it once from the house tokens. Defensive: the pages
        are built lazily, so any widget may not exist yet.
        """
        try:
            surface_2 = resolved.get("surface_2", SURFACE_2)
            surface_1 = resolved.get("surface_1", SURFACE_1)
            line = resolved.get("line_subtle", LINE_SUBTLE)
            line_strong = resolved.get("line_strong", LINE_STRONG)
            text = resolved.get("text", TEXT)
            muted = resolved.get("text_muted", TEXT_MUTED)
            accent = resolved.get("accent", ACCENT)
            for table in (getattr(self, "_views_month_table", None),
                          getattr(self, "_views_week_table", None)):
                if table is not None:
                    table.setStyleSheet(
                        f"QTableWidget {{ background: {surface_2};"
                        f" color: {text};"
                        f" gridline-color: {line}; }}"
                        f"QTableWidget::item {{ color: {text}; }}"
                        f"QTableWidget::item:selected {{"
                        f" background: {accent}; color: #06222B; }}"
                        f"QTableWidget QHeaderView::section {{"
                        f" background: {surface_2}; color: {text}; }}")
            delegate = getattr(self, "_views_month_delegate", None)
            if delegate is not None:
                delegate.set_theme(resolved)
            delegate = getattr(self, "_views_week_delegate", None)
            if delegate is not None:
                delegate.set_theme(resolved)
            delegate = getattr(self, "_views_rich_delegate", None)
            if delegate is not None:
                delegate.set_theme(resolved)
            body = getattr(self, "_views_inbox_body", None)
            if body is not None:
                body.setStyleSheet(
                    f"QTextBrowser {{ background: {surface_2};"
                    f" color: {text};"
                    f" border: 1px solid {line};"
                    f" border-radius: {RADIUS_CARD}px; }}")
            for tabs in (getattr(self, "_views_calendar_tabs", None),
                         getattr(self, "_views_inbox_tabs", None)):
                if tabs is not None:
                    tabs.set_theme(resolved)
            # Fix round (2026-11-25): cards already on screen
            # (Contacts grid, Calls timeline) are born per refresh
            # — re-theme them here too, or a theme change while a
            # page is showing leaves dark cards behind. (The pill
            # fix's "until the next refresh" limitation is not
            # acceptable here: cards are the page content.)
            for card in self.findChildren(_ViewsCard):
                card.set_theme(resolved)
            for drawer in (getattr(self, "_views_calendar_drawer", None),
                           getattr(self, "_views_inbox_drawer", None),
                           getattr(self, "_views_contacts_drawer", None),
                           getattr(self, "_views_calls_drawer", None)):
                if drawer is not None:
                    drawer.setStyleSheet(
                        f"background: {surface_1}; "
                        f"border-left: 1px solid {line_strong};")
            status = getattr(self, "_views_calls_status", None)
            if status is not None:
                status.setStyleSheet(f"color: {muted};")
            for _title in (
                    getattr(self, "_views_calendar_sync", None),
                    getattr(self, "_views_inbox_sync", None)):
                if _title is not None:
                    _title.setStyleSheet(f"color: {muted};")
            # The Agenda date edit's width follows its live face:
            # the builder measured it before the sheet landed (the
            # app default face, 98 px for "Wed 25 Nov 2026"), so
            # the minimum came up 2 px short of the Sora face
            # (116 px) and the year read "202". Re-measured here —
            # this runs on every appearance pass, after the sheet —
            # the width is 116 + 56 = 172 px at the live face.
            _date_edit = getattr(self, "_views_agenda_date", None)
            if _date_edit is not None:
                _dfm = _date_edit.fontMetrics()
                _date_edit.setMinimumWidth(
                    _dfm.horizontalAdvance("Wed 25 Nov 2026") + 56)
        except Exception:  # noqa: BLE001 — appearance never breaks boot
            pass

    # ------------------------------------------------------------------
    # Calendar (index 8)
    # ------------------------------------------------------------------
    def _build_calendar_page(self) -> QWidget:
        page, content = self._page_shell(
            "Calendar",
            "Month, week or agenda — pick a day to see its events.", kicker=True,
        )
        # The page is a row: the main column (header, sync line,
        # view stack, footer) beside the drawer rail. At 640 the
        # drill-in hides the main column while the drawer is open.
        main = QWidget()
        main_lay = QVBoxLayout(main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(10)
        self._views_calendar_main = main
        # Header: view switch (persisted), period nav, sync state,
        # Add event, Refresh. The tabs are the view; the switch
        # never resets the selected day (D-P3-18).
        header = QHBoxLayout()
        header.setSpacing(8)
        self._views_calendar_tabs = HudTabs()
        for label in ("Month", "Week", "Agenda"):
            self._views_calendar_tabs.addTab(label)
        self._views_calendar_tabs.currentChanged.connect(
            self._on_views_cal_view_changed)
        header.addWidget(self._views_calendar_tabs)
        header.addStretch(1)
        self._views_calendar_prev = QPushButton("‹")
        self._views_calendar_prev.setToolTip("Previous")
        self._views_calendar_prev.clicked.connect(
            self._on_views_cal_prev)
        header.addWidget(self._views_calendar_prev)
        self._views_calendar_today = QPushButton("Today")
        self._views_calendar_today.clicked.connect(
            self._on_views_cal_today)
        header.addWidget(self._views_calendar_today)
        self._views_calendar_next = QPushButton("›")
        self._views_calendar_next.setToolTip("Next")
        self._views_calendar_next.clicked.connect(
            self._on_views_cal_next)
        header.addWidget(self._views_calendar_next)
        main_lay.addLayout(header)

        sync_row = QHBoxLayout()
        sync_row.setSpacing(8)
        self._views_calendar_sync = QLabel("")
        self._views_calendar_sync.setObjectName("muted")
        self._views_calendar_sync.setWordWrap(True)
        sync_row.addWidget(self._views_calendar_sync, 1)
        self._views_calendar_connect = self._views_connect_button()
        self._views_calendar_connect.hide()
        sync_row.addWidget(self._views_calendar_connect)
        main_lay.addLayout(sync_row)

        # The three views share one stack; the Month view carries
        # its day-agenda below the grid.
        self._views_calendar_stack = QStackedWidget()
        month_page = QWidget()
        month_lay = QVBoxLayout(month_page)
        month_lay.setContentsMargins(0, 0, 0, 0)
        month_lay.setSpacing(8)
        self._views_month_table = QTableWidget(6, 7)
        self._views_month_table.setHorizontalHeaderLabels(
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        self._views_month_table.verticalHeader().setVisible(False)
        self._views_month_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self._views_month_table.setSelectionBehavior(
            QAbstractItemView.SelectItems)
        self._views_month_table.setSelectionMode(
            QAbstractItemView.SingleSelection)
        self._views_month_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self._views_month_table.verticalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self._views_month_delegate = _ViewsMonthDelegate(
            self._views_month_table)
        self._views_month_table.setItemDelegate(
            self._views_month_delegate)
        self._views_month_table.currentCellChanged.connect(
            self._on_views_month_cell_changed)
        month_lay.addWidget(self._views_month_table, 1)
        self._views_calendar_day_label = QLabel("This day")
        # No colour baked in: the sheet used to carry the dark
        # theme's text colour, so on Light the label read white
        # on light and vanished. The palette carries the colour
        # in both themes now.
        self._views_calendar_day_label.setStyleSheet(
            f"font-size: {TYPE_CARD_TITLE}px; font-weight: 600;")
        month_lay.addWidget(self._views_calendar_day_label)
        self._views_month_agenda = QListWidget()
        self._views_month_agenda.setWordWrap(True)
        self._views_month_agenda.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The day rows carry markup (bold time, muted conflict
        # line): a plain list item shows the tags on screen, so
        # the rows paint through the shared rich-text delegate.
        self._views_rich_delegate = _ViewsRichTextDelegate(
            self._views_month_agenda)
        self._views_month_agenda.setItemDelegate(
            self._views_rich_delegate)
        self._views_month_agenda.itemActivated.connect(
            self._on_views_agenda_row_picked)
        # The agenda hugs its content (see _views_hug_month_agenda):
        # the grid takes the freed height, so the rows grow and the
        # list never sits half-empty inside a tall box.
        month_lay.addWidget(self._views_month_agenda, 0)
        self._views_calendar_stack.addWidget(month_page)

        week_page = QWidget()
        week_lay = QVBoxLayout(week_page)
        week_lay.setContentsMargins(0, 0, 0, 0)
        week_lay.setSpacing(8)
        self._views_week_table = QTableWidget(25, 8)
        self._views_week_table.setHorizontalHeaderLabels(
            ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        self._views_week_table.verticalHeader().setVisible(False)
        self._views_week_table.setEditTriggers(
            QAbstractItemView.NoEditTriggers)
        self._views_week_table.setSelectionBehavior(
            QAbstractItemView.SelectItems)
        self._views_week_table.setSelectionMode(
            QAbstractItemView.SingleSelection)
        self._views_week_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        # The hour gutter is fixed narrow: the time column only
        # ever holds "All day" / "23:00", so it gives its share
        # back to the seven day columns — a "9:00 AM Q3 review"
        # used to elide to "9:00 AM Q3 ..." in the equal split.
        self._views_week_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Fixed)
        self._views_week_table.setColumnWidth(0, 64)
        self._views_week_delegate = _ViewsWeekDelegate(
            self._views_week_table)
        self._views_week_table.setItemDelegate(
            self._views_week_delegate)
        self._views_week_table.cellClicked.connect(
            self._on_views_week_cell_clicked)
        # The now-line moves with the grid: a scroll redraws it
        # (its geometry is viewport-relative), so it never sits
        # over the wrong hour after the user scrolls.
        self._views_week_table.verticalScrollBar().valueChanged.connect(
            lambda _v: self._views_draw_now_line())
        self._views_week_table.horizontalScrollBar().valueChanged.connect(
            lambda _v: self._views_draw_now_line())
        week_lay.addWidget(self._views_week_table, 1)
        # The now-line: a 2 px accent rule across the whole day
        # grid at the current time, with a "Now" tag in the hour
        # gutter — a bare segment in one column read as a glitch.
        # Moved by the page-scoped 60 s timer (S-P3-04 deviation
        # — no window-wide vitals tick exists to reuse). Children
        # of the viewport so they track scrolling; hidden unless
        # today is in the shown week.
        self._views_now_line = QLabel(self._views_week_table.viewport())
        self._views_now_line.setStyleSheet(
            f"background: {ACCENT};")
        self._views_now_line.setFixedHeight(2)
        self._views_now_line.hide()
        self._views_now_tag = _ViewsNowTag(
            self._views_week_table.viewport())
        self._views_now_tag.hide()
        self._views_calendar_stack.addWidget(week_page)

        agenda_page = QWidget()
        agenda_lay = QVBoxLayout(agenda_page)
        agenda_lay.setContentsMargins(0, 0, 0, 0)
        agenda_lay.setSpacing(8)
        agenda_filter = QHBoxLayout()
        agenda_filter.setSpacing(8)
        agenda_filter.addWidget(QLabel("Day:"))
        self._views_agenda_date = QDateEdit()
        self._views_agenda_date.setCalendarPopup(True)
        # A readable date, not bare numbers: the default locale
        # format rendered "25 11 2026".
        self._views_agenda_date.setDisplayFormat("ddd d MMM yyyy")
        # Wide enough for its own text: the size hint came up
        # short and the year read "Wed 25 Nov 202" — the full
        # "Wed 25 Nov 2026" plus room for the popup button and
        # the frame, measured on the edit's own face.
        _date_fm = self._views_agenda_date.fontMetrics()
        self._views_agenda_date.setMinimumWidth(
            _date_fm.horizontalAdvance("Wed 25 Nov 2026") + 56)
        self._views_agenda_date.setDate(QDate.currentDate())
        self._views_agenda_date.dateChanged.connect(
            self._on_views_agenda_day_changed)
        agenda_filter.addWidget(self._views_agenda_date)
        self._views_agenda_today = QPushButton("Today")
        self._views_agenda_today.clicked.connect(
            self._on_views_agenda_today)
        agenda_filter.addWidget(self._views_agenda_today)
        self._views_agenda_all = QPushButton("Show all")
        self._views_agenda_all.clicked.connect(
            self._on_views_agenda_show_all)
        agenda_filter.addWidget(self._views_agenda_all)
        agenda_filter.addStretch(1)
        agenda_lay.addLayout(agenda_filter)
        self._views_agenda_filter_label = QLabel("")
        self._views_agenda_filter_label.setObjectName("muted")
        agenda_lay.addWidget(self._views_agenda_filter_label)
        self._views_agenda_list = QListWidget()
        self._views_agenda_list.setWordWrap(True)
        self._views_agenda_list.setItemDelegate(
            self._views_rich_delegate)
        self._views_agenda_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._views_agenda_list.itemActivated.connect(
            self._on_views_agenda_row_picked)
        agenda_lay.addWidget(self._views_agenda_list, 1)
        # A one-line count under the list, so the page ends on a
        # fact instead of a void: "4 events · 3 today · 1 tomorrow".
        self._views_agenda_summary = QLabel("")
        self._views_agenda_summary.setObjectName("muted")
        agenda_lay.addWidget(self._views_agenda_summary)
        self._views_calendar_stack.addWidget(agenda_page)
        main_lay.addWidget(self._views_calendar_stack, 1)

        # Footer: Add event + Refresh. The not-connected and
        # empty states live in the lists themselves (D-P3-17).
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._views_calendar_add = QPushButton("Add event")
        self._views_calendar_add.clicked.connect(
            self._views_open_create_drawer)
        footer.addWidget(self._views_calendar_add)
        footer.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_calendar_page)
        footer.addWidget(refresh)
        main_lay.addLayout(footer)

        # The detail / create drawer: one shared ContextDrawer
        # (D-P3-19), a rail beside the page, hidden until opened.
        self._views_calendar_drawer = ContextDrawer(title="Event")
        self._views_calendar_drawer.setObjectName("viewsCalendarDrawer")
        self._views_calendar_drawer.hide()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(main, 1)
        row.addWidget(self._views_calendar_drawer)
        content.addLayout(row)
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "surface_1": SURFACE_1,
            "line_subtle": LINE_SUBTLE, "line_strong": LINE_STRONG,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})

        # State: the selected day survives refreshes and view
        # switches; the view persists in calendar/view (D-P3-16);
        # the Agenda day filter starts unset (whole window shown).
        self._views_calendar_events: list[dict] = []
        self._views_calendar_page_date = datetime.date.today().replace(day=1)
        self._views_calendar_selected_date = datetime.date.today()
        self._views_calendar_view = "Month"
        self._views_agenda_day = None
        self._views_calendar_can_write = True
        self._views_calendar_current_event = None
        self._views_now_timer = QTimer(self)
        self._views_now_timer.setInterval(60_000)
        self._views_now_timer.timeout.connect(self._views_draw_now_line)
        try:
            from voice_assistant.core.gui_facade import qsettings_get
            saved = str(qsettings_get("calendar/view") or "Month")
            if saved in ("Month", "Week", "Agenda"):
                self._views_calendar_view = saved
        except Exception:  # noqa: BLE001 — an unreadable view
            pass  # setting reads as Month, never breaks boot.
        self._views_calendar_tabs.setCurrentIndex(
            ("Month", "Week", "Agenda").index(self._views_calendar_view))
        self._views_calendar_stack.setCurrentIndex(
            ("Month", "Week", "Agenda").index(self._views_calendar_view))
        try:
            if not getattr(self, "_views_stack_connected", False):
                self.pages.currentChanged.connect(
                    self._on_views_stack_changed)
                self._views_stack_connected = True
        except Exception:  # noqa: BLE001 — the timer is then
            pass  # managed by the refresh alone.
        return page

    def _refresh_calendar_page(self) -> None:
        from voice_assistant.core.gui_facade import google_services

        if getattr(self, "_views_month_table", None) is None:
            return
        # A refresh never resets UI state (D-P3-18): the view, the
        # selected day, the Agenda filter and the scroll positions
        # all survive — only the data is re-read.
        try:
            month_scroll = self._views_month_agenda.verticalScrollBar().value()
            agenda_scroll = self._views_agenda_list.verticalScrollBar().value()
        except Exception:  # noqa: BLE001
            month_scroll = agenda_scroll = 0
        start, end = self._views_calendar_span()
        try:
            events = google_services.events_in_range(start, end)
        except Exception:  # noqa: BLE001 — a failed read shows the
            self._views_calendar_sync.setText(  # failure with Retry;
                "Couldn't read your calendar just now — try Refresh.")
            return  # it never blanks the page to an empty state.
        self._views_calendar_events = events or []
        try:
            connected = bool(google_services.is_connected())
        except Exception:  # noqa: BLE001
            connected = False
        self._views_calendar_can_write = (
            self._views_can_write("calendar") if connected else False)
        private = self._views_private_on()
        # Sync state in the header (D-P3-13): connected, read-only
        # (scope sentence verbatim, write controls absent), or not
        # connected (copy + Connect button — never a bare line).
        if not connected:
            self._views_calendar_sync.setText(_CONNECT_GOOGLE)
            self._views_calendar_connect.show()
            self._views_calendar_add.setVisible(False)
        elif not self._views_calendar_can_write:
            self._views_calendar_sync.setText(
                self._views_scope_sentence("adding events"))
            self._views_calendar_connect.hide()
            self._views_calendar_add.setVisible(False)
        else:
            self._views_calendar_sync.setText(
                "Connected — Private mode is on. Times are shown; "
                "titles are hidden." if private else "Connected.")
            self._views_calendar_connect.hide()
            self._views_calendar_add.setVisible(not private)
        if not connected:
            # Hunt fix (c) item 5: the connect line lives ONLY in
            # the header sync slot above (set just above, with the
            # Connect button). The panes show their own real empty
            # states — the old fills repeated the line in the Month
            # "This day" list, the Agenda list, and the Week
            # all-day cell (where it clipped to "Connect Google
            # i…"). The empty fills take no line any more.
            self._views_fill_month_empty()
            self._views_fill_agenda_empty()
            self._views_fill_week_empty()
            self._views_manage_now_timer()
            return
        self._views_fill_month()
        self._views_fill_week()
        self._views_fill_day_agenda()
        self._views_fill_agenda_list()
        try:
            self._views_month_agenda.verticalScrollBar().setValue(month_scroll)
            self._views_agenda_list.verticalScrollBar().setValue(agenda_scroll)
        except Exception:  # noqa: BLE001
            pass
        self._views_manage_now_timer()

    def _views_calendar_span(self):
        """The read window for the current view: the displayed
        month (plus the shown week when it spills past it), with
        7 days of margin — conflicts and busy blocks are computed
        from this loaded window only."""
        first = self._views_calendar_page_date.replace(day=1)
        last_day = _calendar.monthrange(first.year, first.month)[1]
        last = first.replace(day=last_day)
        monday = self._views_calendar_selected_date - datetime.timedelta(
            days=self._views_calendar_selected_date.weekday())
        sunday = monday + datetime.timedelta(days=6)
        start = min(first, monday)
        end = max(last, sunday) + datetime.timedelta(days=7)
        return start, end

    def _views_event_conflicts(self, events):
        """Same-start timed pairs in the loaded window → {id:
        conflict line}. Two timed events starting at the same
        instant overlap at that instant, whatever their lengths;
        the read carries no end times, so a different start is
        never called a conflict. All-day events never conflict
        with each other — they coexist by nature."""
        by_start: dict = {}
        for event in events or []:
            start = event.get("start")
            if event.get("all_day") or not isinstance(
                    start, datetime.datetime):
                continue
            by_start.setdefault(start, []).append(event)
        out: dict = {}
        for start, group in by_start.items():
            if len(group) < 2:
                continue
            for event in group:
                others = [g.get("title") or "(untitled event)"
                          for g in group if g is not event]
                out[event.get("id")] = (
                    f"Conflict — also starts at {_event_time(start)}: "
                    + ", ".join(others))
        return out

    def _views_event_row(self, event: dict, conflicts: dict) -> QListWidgetItem:
        """The ONE event-row builder (D-P3-03): Month agenda and
        Agenda list render the same rows. In private mode the title
        is blanked to "Private event" and the time is kept. The
        row is rich text: the time reads bold, the conflict note
        reads muted underneath, an all-day event says so boldly
        — a plain line per event read as bare text. Titles are
        escaped: user text must never become markup."""
        if self._views_private_on():
            start = event.get("start")
            if isinstance(start, datetime.datetime):
                stamp = ("All day" if event.get("all_day")
                         else _event_time(start))
                stamp += f" — {start.strftime('%a %d %b')}"
            else:
                stamp = ""
            text = f"<b>{html.escape(stamp)}</b> — Private event" \
                if stamp else "<b>Private event</b>"
            text = (f"<span style=\"color:{_VIEWS_ROW_INK}\">"
                    + text + "</span>")
        else:
            start = event.get("start")
            title = html.escape(
                event.get("title") or "(untitled event)")
            location = html.escape(event.get("location") or "")
            if isinstance(start, datetime.datetime):
                stamp = ("All day" if event.get("all_day")
                         else _event_time(start))
                stamp += f" — {start.strftime('%a %d %b')}"
            else:
                stamp = ""
            text = f"<b>{html.escape(stamp)}</b> — {title}" \
                if stamp else f"<b>{title}</b>"
            if location:
                text += f" · {location}"
            # The event line carries its ink inline (see
            # _VIEWS_ROW_INK): the delegate substitutes the
            # paint-time colour, so the line reads at full
            # brightness — themed text, dark on a selected row.
            text = (f"<span style=\"color:{_VIEWS_ROW_INK}\">"
                    + text + "</span>")
            conflict = conflicts.get(event.get("id"))
            if conflict:
                text += ("<br><span style=\"color:"
                         + TEXT_MUTED + "\">"
                         + html.escape(conflict) + "</span>")
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, event.get("id") or "")
        return item

    def _views_fill_month_empty(self) -> None:
        # The grid keeps its day numbers even with nothing to read:
        # the dates come from the calendar alone, never from Google,
        # so the offline / failed-read grid is still a usable month
        # skeleton — only the event dots are absent (zero counts).
        # Hunt fix (c) item 5: the day agenda shows the connected
        # empty state ("No events this day."), not the connect
        # line — that line lives only in the header sync slot.
        # The Agenda list is the agenda fill's job, not this one.
        table = self._views_month_table
        # Hunt fix (c) N2: block signals BEFORE clear(). clear()
        # fires currentCellChanged(-1, -1), and the month-cell
        # handler reads that as "8 days before grid start" — the
        # previous month — so every refresh walked the page back
        # one month (Nov -> Feb over 9 refreshes). The connected
        # path (_views_fill_month) already blocks first.
        table.blockSignals(True)
        table.clear()
        table.setHorizontalHeaderLabels(
            ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        first = self._views_calendar_page_date.replace(day=1)
        grid_start = first - datetime.timedelta(days=first.weekday())
        try:
            for row in range(6):
                for col in range(7):
                    day = grid_start + datetime.timedelta(days=row * 7 + col)
                    item = QTableWidgetItem(str(day.day))
                    item.setData(Qt.ItemDataRole.UserRole + 1, 0)
                    item.setData(Qt.ItemDataRole.UserRole + 2,
                                 day.month != first.month)
                    item.setData(Qt.ItemDataRole.UserRole + 3, [])
                    table.setItem(row, col, item)
            selected = self._views_calendar_selected_date
            offset = (selected - grid_start).days
            if 0 <= offset < 42:
                table.setCurrentCell(offset // 7, offset % 7)
        finally:
            table.blockSignals(False)
        self._views_month_agenda.clear()
        self._views_month_agenda.addItem("No events this day.")
        selected = self._views_calendar_selected_date
        self._views_calendar_day_label.setText(
            selected.strftime("This day — %A %d %B %Y")
            + " · No events")
        self._views_hug_month_agenda()

    def _views_hug_month_agenda(self) -> None:
        """Pin the day agenda to its content height (capped): the
        month grid above it takes whatever height is freed, so
        the rows grow and the list never sits half-empty inside
        a tall box."""
        listing = self._views_month_agenda
        total = sum(listing.sizeHintForRow(i)
                    for i in range(listing.count()))
        listing.setMaximumHeight(
            min(240, total + 2 * listing.frameWidth() + 2))

    def _views_fill_agenda_empty(self) -> None:
        # Hunt fix (c) item 5: the Agenda list shows the
        # connected empty state ("No events in this window." +
        # "No events loaded."), not the connect line — that line
        # lives only in the header sync slot. The Month "This
        # day" list is the month fill's job, not this one.
        self._views_agenda_list.clear()
        self._views_agenda_list.addItem("No events in this window.")
        self._views_agenda_summary.setText("No events loaded.")
        self._views_hug_month_agenda()

    def _views_fill_week_empty(self) -> None:
        # Hunt fix (c) item 5 + N1: the all-day cell stays empty
        # — the connect line lived in the header, and stuffing it
        # into the Monday all-day cell clipped it to "Connect
        # Google i…". The week grid is an empty skeleton (hour
        # labels, no events), like the connected empty week.
        table = self._views_week_table
        table.clear()
        table.setHorizontalHeaderLabels(
            ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
        for row in range(25):
            table.setItem(row, 0, QTableWidgetItem(
                "" if row == 0 else f"{row - 1:02d}:00"))
        delegate = getattr(self, "_views_week_delegate", None)
        if delegate is not None:
            delegate.set_today_column(-1)

    def _views_fill_month(self) -> None:
        """The month grid: 6 weeks × 7 days, dots are the one
        marking. The selected day stays selected across refreshes."""
        table = self._views_month_table
        first = self._views_calendar_page_date.replace(day=1)
        grid_start = first - datetime.timedelta(days=first.weekday())
        counts: dict = {}
        titles: dict = {}
        for event in self._views_calendar_events:
            start = event.get("start")
            if isinstance(start, datetime.datetime):
                counts[start.date()] = counts.get(start.date(), 0) + 1
                titles.setdefault(start.date(), []).append(
                    "Private event" if self._views_private_on()
                    else (event.get("title") or "(untitled event)"))
        table.blockSignals(True)
        try:
            for row in range(6):
                for col in range(7):
                    day = grid_start + datetime.timedelta(days=row * 7 + col)
                    item = QTableWidgetItem(str(day.day))
                    item.setData(Qt.ItemDataRole.UserRole + 1,
                                 counts.get(day, 0))
                    item.setData(Qt.ItemDataRole.UserRole + 2,
                                 day.month != first.month)
                    item.setData(Qt.ItemDataRole.UserRole + 3,
                                 titles.get(day, []))
                    table.setItem(row, col, item)
            selected = self._views_calendar_selected_date
            offset = (selected - grid_start).days
            if 0 <= offset < 42:
                table.setCurrentCell(offset // 7, offset % 7)
        finally:
            table.blockSignals(False)

    def _views_fill_day_agenda(self) -> None:
        """The selected day's events under the month grid."""
        selected = self._views_calendar_selected_date
        listing = self._views_month_agenda
        listing.clear()
        conflicts = self._views_event_conflicts(
            self._views_calendar_events)
        found = 0
        for event in self._views_calendar_events:
            start = event.get("start")
            if isinstance(start, datetime.datetime) \
                    and start.date() == selected:
                listing.addItem(self._views_event_row(event, conflicts))
                found += 1
        if not found:
            listing.addItem("No events this day.")
        # The day panel says how many events the day holds, so
        # the list never reads as bare lines under a bare date.
        self._views_calendar_day_label.setText(
            selected.strftime("This day — %A %d %B %Y")
            + (f" · {found} event{'s' if found != 1 else ''}"
               if found else " · No events"))
        self._views_hug_month_agenda()

    def _views_fill_agenda_list(self) -> None:
        """The Agenda: the window's events chronologically — or one
        day when the day filter is set (D-P3-03: the Agenda scoped
        to one day IS the Day view). The rows are roomy (a plain
        line per event read as bare text), the helper says in
        plain words what the list is, and a count line closes the
        page so it ends on a fact instead of a void."""
        listing = self._views_agenda_list
        listing.clear()
        conflicts = self._views_event_conflicts(
            self._views_calendar_events)
        day = self._views_agenda_day
        if day is not None:
            self._views_agenda_filter_label.setText(
                f"Showing {day.strftime('%A %d %B %Y')} — just that day.")
        else:
            self._views_agenda_filter_label.setText(
                "All the events loaded for this month — pick a day "
                "above to see just that one day.")
        found = False
        for event in self._views_calendar_events:
            start = event.get("start")
            if not isinstance(start, datetime.datetime):
                continue
            if day is not None and start.date() != day:
                continue
            row_item = self._views_event_row(event, conflicts)
            # Tall, roomy rows — 64 px left the four Agenda rows
            # as thin lines in a tall list with a void below them.
            # Month day-agenda rows stay at the delegate's natural
            # height: 76 px there would shrink the month grid
            # below the ~50 px its in-cell titles need.
            row_item.setSizeHint(QSize(0, 76))
            listing.addItem(row_item)
            found = True
        if not found:
            listing.addItem(
                "No events this day." if day is not None
                else "No events in this window.")
        self._views_agenda_summary.setText(
            self._views_agenda_summary_text())

    def _views_agenda_summary_text(self) -> str:
        """"4 events · 3 today · 1 tomorrow" — the loaded window
        counted, so the Agenda page ends on a fact."""
        events = self._views_calendar_events or []
        total = 0
        n_today = 0
        n_tomorrow = 0
        today = datetime.date.today()
        tomorrow = today + datetime.timedelta(days=1)
        for event in events:
            start = event.get("start")
            if not isinstance(start, datetime.datetime):
                continue
            total += 1
            if start.date() == today:
                n_today += 1
            elif start.date() == tomorrow:
                n_tomorrow += 1
        if total == 0:
            return "No events loaded."
        parts = [f"{total} event{'s' if total != 1 else ''}"]
        if n_today:
            parts.append(f"{n_today} today")
        if n_tomorrow:
            parts.append(f"{n_tomorrow} tomorrow")
        return " · ".join(parts)

    def _views_fill_week(self) -> None:
        """The week strip: all-day row on top, timed events on an
        hour axis. All-day events are never rendered as midnight
        timed blocks (P3-12)."""
        table = self._views_week_table
        monday = self._views_calendar_selected_date - datetime.timedelta(
            days=self._views_calendar_selected_date.weekday())
        days = [monday + datetime.timedelta(days=i) for i in range(7)]
        table.blockSignals(True)
        try:
            table.clear()
            table.setHorizontalHeaderLabels(
                ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])
            for row in range(25):
                table.setItem(row, 0, QTableWidgetItem(
                    "All day" if row == 0 else f"{row - 1:02d}:00"))
            today = datetime.date.today()
            for col, day in enumerate(days, start=1):
                header = table.horizontalHeaderItem(col)
                if header is not None:
                    label = day.strftime("%a %d")
                    if day == today:
                        label = f"● {label}"
                    header.setText(label)
            delegate = getattr(self, "_views_week_delegate", None)
            if delegate is not None:
                delegate.set_today_column(
                    days.index(today) + 1 if today in days else -1)
            grouped: dict = {}
            for event in self._views_calendar_events:
                start = event.get("start")
                if not isinstance(start, datetime.datetime):
                    continue
                if start.date() not in days:
                    continue
                col = days.index(start.date()) + 1
                row = 0 if event.get("all_day") else start.hour + 1
                grouped.setdefault((row, col), []).append(event)
            # A cell holds ONE item, so two events at the same
            # start used to overwrite each other — the second
            # write won and the first vanished from the grid.
            # Grouped, every event in the cell is a line of the
            # one item, and the row grows to show them all.
            default_h = table.verticalHeader().defaultSectionSize()
            for row in range(25):
                table.setRowHeight(row, default_h)
            line_h = table.fontMetrics().height()
            for (row, col), group in sorted(grouped.items()):
                lines = []
                for event in group:
                    title = ("Private event" if self._views_private_on()
                             else (event.get("title")
                                   or "(untitled event)"))
                    if event.get("all_day"):
                        lines.append(title)
                    else:
                        lines.append(
                            f"{_event_time(event.get('start'))} {title}")
                item = QTableWidgetItem("\n".join(lines))
                item.setData(Qt.ItemDataRole.UserRole,
                             group[0].get("id") or "")
                table.setItem(row, col, item)
                if len(lines) > 1:
                    table.setRowHeight(row, len(lines) * line_h + 8)
        finally:
            table.blockSignals(False)

    # -- Calendar navigation / view switching ----------------------
    def _on_views_cal_view_changed(self, index: int) -> None:
        view = ("Month", "Week", "Agenda")[index]
        self._views_calendar_view = view
        self._views_calendar_stack.setCurrentIndex(index)
        try:
            from voice_assistant.core.gui_facade import qsettings_set
            qsettings_set("calendar/view", view)
        except Exception:  # noqa: BLE001 — persistence is
            pass  # best-effort; the view still switches.
        self._refresh_calendar_page()

    def _on_views_cal_prev(self) -> None:
        self._views_cal_step(-1)

    def _on_views_cal_next(self) -> None:
        self._views_cal_step(1)

    def _views_cal_step(self, delta: int) -> None:
        view = self._views_calendar_view
        if view == "Month":
            year = self._views_calendar_page_date.year
            month = self._views_calendar_page_date.month + delta
            while month < 1:
                month += 12
                year -= 1
            while month > 12:
                month -= 12
                year += 1
            self._views_calendar_page_date = datetime.date(year, month, 1)
            self._views_calendar_selected_date = datetime.date(
                year, month, min(
                    self._views_calendar_selected_date.day,
                    _calendar.monthrange(year, month)[1]))
        elif view == "Week":
            self._views_calendar_selected_date += datetime.timedelta(
                days=7 * delta)
            self._views_calendar_page_date = \
                self._views_calendar_selected_date.replace(day=1)
        else:  # Agenda: step the day filter when set, else the month.
            if self._views_agenda_day is not None:
                self._views_agenda_day += datetime.timedelta(days=delta)
                self._views_agenda_date.setDate(
                    QDate(self._views_agenda_day.year,
                          self._views_agenda_day.month,
                          self._views_agenda_day.day))
            else:
                self._views_cal_step_month(delta)
                return
        self._refresh_calendar_page()

    def _views_cal_step_month(self, delta: int) -> None:
        year = self._views_calendar_page_date.year
        month = self._views_calendar_page_date.month + delta
        while month < 1:
            month += 12
            year -= 1
        while month > 12:
            month -= 12
            year += 1
        self._views_calendar_page_date = datetime.date(year, month, 1)

    def _on_views_cal_today(self) -> None:
        today = datetime.date.today()
        self._views_calendar_selected_date = today
        self._views_calendar_page_date = today.replace(day=1)
        self._refresh_calendar_page()

    def _on_views_month_cell_changed(self, row: int, col: int) -> None:
        # Hunt fix (c) N2: a cleared table reports (-1, -1); with
        # no guard that read as grid_start - 8 days and followed
        # the previous month. A negative cell is no selection.
        if row < 0 or col < 0:
            return
        first = self._views_calendar_page_date.replace(day=1)
        grid_start = first - datetime.timedelta(days=first.weekday())
        day = grid_start + datetime.timedelta(days=row * 7 + col)
        if day.month != first.month:
            # Tapping an other-month day follows it, like the old
            # calendar widget did.
            self._views_calendar_page_date = day.replace(day=1)
            self._views_calendar_selected_date = day
            self._refresh_calendar_page()
            return
        self._views_calendar_selected_date = day
        self._views_fill_day_agenda()

    def _on_views_agenda_row_picked(self, item: QListWidgetItem) -> None:
        event_id = item.data(Qt.ItemDataRole.UserRole) or ""
        if not event_id:
            return
        event = next((e for e in self._views_calendar_events
                      if str(e.get("id") or "") == str(event_id)), None)
        if event is not None:
            self._views_open_event_drawer(event, "detail")

    def _on_views_week_cell_clicked(self, row: int, col: int) -> None:
        if col == 0:
            return
        item = self._views_week_table.item(row, col)
        if item is None:
            return
        event_id = item.data(Qt.ItemDataRole.UserRole) or ""
        if not event_id:
            return
        event = next((e for e in self._views_calendar_events
                      if str(e.get("id") or "") == str(event_id)), None)
        if event is not None:
            self._views_open_event_drawer(event, "detail")

    def _on_views_agenda_day_changed(self, date: QDate) -> None:
        self._views_agenda_day = date.toPython()
        self._views_fill_agenda_list()

    def _on_views_agenda_today(self) -> None:
        today = datetime.date.today()
        self._views_agenda_day = today
        self._views_agenda_date.setDate(
            QDate(today.year, today.month, today.day))

    def _on_views_agenda_show_all(self) -> None:
        self._views_agenda_day = None
        self._views_fill_agenda_list()

    # -- Week now-line (page-scoped 60 s timer) --------------------
    def _on_views_stack_changed(self, index: int) -> None:
        self._views_manage_now_timer()

    def _views_manage_now_timer(self) -> None:
        """Start the now-line timer only while the Week view is the
        visible page; stop it on hide or view switch. The timer is
        page-scoped — it never runs while the user is elsewhere."""
        try:
            current = self.pages.currentIndex()
        except Exception:  # noqa: BLE001
            current = 8
        if current == PAGE_CALENDAR \
                and self._views_calendar_view == "Week":
            if not self._views_now_timer.isActive():
                self._views_now_timer.start()
            self._views_draw_now_line()
        else:
            if self._views_now_timer.isActive():
                self._views_now_timer.stop()
            try:
                self._views_now_line.hide()
                tag = getattr(self, "_views_now_tag", None)
                if tag is not None:
                    tag.hide()
            except Exception:  # noqa: BLE001
                pass

    def _views_draw_now_line(self) -> None:
        """The now-line: a 2 px accent rule across the whole day
        grid at the current time, with a "Now" tag in the hour
        gutter. A bare segment in one column read as a glitch;
        spanning the grid and labelling it says what it is.
        Hidden unless today is in the shown week and the Week
        view is visible."""
        line = getattr(self, "_views_now_line", None)
        tag = getattr(self, "_views_now_tag", None)
        table = getattr(self, "_views_week_table", None)
        if line is None or table is None:
            return

        def _hide():
            line.hide()
            if tag is not None:
                tag.hide()

        if self._views_calendar_view != "Week":
            _hide()
            return
        monday = self._views_calendar_selected_date - datetime.timedelta(
            days=self._views_calendar_selected_date.weekday())
        today = datetime.date.today()
        if today < monday or today > monday + datetime.timedelta(days=6):
            _hide()
            return
        try:
            x = table.columnViewportPosition(1)
            end = (table.columnViewportPosition(7)
                   + table.columnWidth(7))
            width = end - x
            top = table.rowViewportPosition(1)
            bottom = table.rowViewportPosition(25)
            if bottom <= top:
                bottom = top + 24 * table.rowHeight(1)
            now = datetime.datetime.now()
            frac = (now.hour * 60 + now.minute) / (24 * 60)
            y = top + int(frac * (bottom - top))
            line.setGeometry(x, y, width, 2)
            line.show()
            line.raise_()
            if tag is not None:
                fm = tag.fontMetrics()
                tw = fm.horizontalAdvance("Now") + 10
                th = fm.height() + 4
                tag.setFixedSize(tw, th)
                tag.move(max(0, x - tw - 4), max(0, y - th // 2))
                tag.show()
                tag.raise_()
        except Exception:  # noqa: BLE001 — the line is cosmetic;
            _hide()  # it never breaks the page.

    # -- Calendar drawer: detail / edit / reschedule / create -----
    def _views_drawer_clear(self, drawer) -> None:
        """Empty a drawer before refilling it (the drawer is one
        component reused across modes). Recurses into nested layouts:
        the contact form stacks each detail's key/value labels in a
        QVBoxLayout inside the row QHBoxLayout, and a one-level clear
        left those labels behind — opening a second contact showed
        the first contact's details still in the drawer (bug 22)."""
        def _clear(layout):
            while layout.count():
                child = layout.takeAt(0)
                widget = child.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
                sub = child.layout()
                if sub is not None:
                    _clear(sub)

        _clear(drawer.content_layout())

    def _views_drawer_close_row(self, drawer, main) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(
            lambda: self._views_close_drawer(drawer, main))
        row.addWidget(close)
        return row

    def _views_open_event_drawer(self, event: dict, mode: str) -> None:
        """The event drawer (detail / edit / reschedule). In
        private mode no drawer opens with user content: the
        detail shows the times only, and the write buttons are
        absent."""
        if self._views_private_on() and mode != "detail":
            return
        self._views_calendar_current_event = event
        drawer = self._views_calendar_drawer
        self._views_drawer_clear(drawer)
        lay = drawer.content_layout()
        private = self._views_private_on()
        start = event.get("start")
        title = ("Private event" if private
                 else (event.get("title") or "(untitled event)"))
        head = QLabel(title)
        head.setObjectName("sectionTitle")
        head.setWordWrap(True)
        lay.addWidget(head)
        if isinstance(start, datetime.datetime):
            if event.get("all_day"):
                when = start.strftime("%A %d %B %Y") + " — All day"
            else:
                when = start.strftime("%A %d %B %Y, %I:%M %p").lstrip("0")
            lay.addWidget(QLabel(when))
        if not private and event.get("location"):
            lay.addWidget(QLabel(f"Where: {event.get('location')}"))
        conflicts = self._views_event_conflicts(
            self._views_calendar_events)
        conflict = conflicts.get(event.get("id"))
        if conflict and not private:
            warn = QLabel(conflict)
            warn.setWordWrap(True)
            lay.addWidget(warn)
        if mode == "detail":
            if private:
                note = QLabel(
                    "Private mode is on — the title and location "
                    "are hidden. Times are shown.")
                note.setObjectName("muted")
                note.setWordWrap(True)
                lay.addWidget(note)
            else:
                row = QHBoxLayout()
                if self._views_calendar_can_write:
                    edit = QPushButton("Edit")
                    edit.clicked.connect(
                        lambda: self._views_open_event_drawer(event, "edit"))
                    row.addWidget(edit)
                    move = QPushButton("Reschedule")
                    move.clicked.connect(
                        lambda: self._views_open_event_drawer(
                            event, "reschedule"))
                    row.addWidget(move)
                    cancel = QPushButton("Cancel event")
                    cancel.clicked.connect(
                        lambda: self._views_cal_cancel(event))
                    row.addWidget(cancel)
                lay.addLayout(row)
            lay.addLayout(self._views_drawer_close_row(
                drawer, self._views_calendar_main))
        elif mode == "edit":
            self._views_cal_edit_form(lay, event)
        elif mode == "reschedule":
            self._views_cal_reschedule_form(lay, event)
        lay.addStretch(1)
        self._views_open_drawer(drawer, self._views_calendar_main)

    def _views_cal_edit_form(self, lay, event: dict) -> None:
        """Edit title / description / location. There is no update
        function in the backend, so an edit is create-new +
        cancel-old (P3-25): create first; if the create fails the
        old event is untouched; if the cancel fails the drawer says
        so with a Retry, and never silently leaves two events."""
        lay.addWidget(QLabel("Edit this event — saving creates the "
                             "edited event first, then removes the old one."))
        title_in = QLineEdit(event.get("title") or "")
        title_in.setPlaceholderText("Title")
        lay.addWidget(title_in)
        desc_in = QLineEdit("")
        desc_in.setPlaceholderText("Description (optional)")
        lay.addWidget(desc_in)
        loc_in = QLineEdit(event.get("location") or "")
        loc_in.setPlaceholderText("Location (optional)")
        lay.addWidget(loc_in)
        receipt = QLabel("")
        receipt.setObjectName("muted")
        receipt.setWordWrap(True)
        lay.addWidget(receipt)
        row = QHBoxLayout()
        save = QPushButton("Save changes")
        save.clicked.connect(lambda: self._views_cal_edit_save(
            event, title_in.text(), desc_in.text(), loc_in.text(),
            receipt))
        row.addWidget(save)
        back = QPushButton("Back")
        back.clicked.connect(
            lambda: self._views_open_event_drawer(event, "detail"))
        row.addWidget(back)
        lay.addLayout(row)

    def _views_cal_edit_save(self, event, title, description, location,
                             receipt) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        title = (title or "").strip()
        if not title:
            receipt.setText("The event needs a title.")
            return
        start = event.get("start")
        if not isinstance(start, datetime.datetime):
            receipt.setText("This event has no time to carry over.")
            return
        end = start + datetime.timedelta(hours=1)
        sentence = (f"Save the edited event '{title}' on "
                    f"{start.strftime('%d %b %Y, %I:%M %p').lstrip('0')} "
                    f"and remove the old event "
                    f"'{event.get('title') or ''}'.")
        if not self._views_confirm_write(
                "create_calendar_event", "Edit event", sentence):
            receipt.setText("Not saved — the old event is untouched.")
            return
        try:
            created = google_services.create_event(
                title, start.isoformat(), end.isoformat(),
                description=(description or "").strip(),
                location=(location or "").strip())
        except Exception as exc:  # noqa: BLE001
            receipt.setText(f"Couldn't save the edited event: {exc}")
            return
        if outcome_kind(created) == "failed":
            receipt.setText(str(created))
            return
        try:
            gone = google_services.cancel_event(
                event_id=str(event.get("id") or ""))
        except Exception as exc:  # noqa: BLE001
            gone = f"Couldn't remove the old event: {exc}"
        if outcome_kind(gone) == "failed":
            receipt.setText(
                "The new event is created but the old one could "
                f"not be removed: {gone} — press Retry to try "
                "removing it again.")
            retry = QPushButton("Retry removing the old event")
            retry.clicked.connect(lambda: self._views_cal_cancel_retry(
                event, receipt))
            try:
                receipt.parentWidget().layout().addWidget(retry)
            except Exception:  # noqa: BLE001
                pass
            self._refresh_calendar_page()
            return
        receipt.setText(str(created))
        self._views_toast(str(created))
        self._views_close_drawer(self._views_calendar_drawer,
                                 self._views_calendar_main)
        self._refresh_calendar_page()

    def _views_cal_cancel_retry(self, event, receipt) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        try:
            gone = google_services.cancel_event(
                event_id=str(event.get("id") or ""))
        except Exception as exc:  # noqa: BLE001
            gone = f"Couldn't remove the old event: {exc}"
        if outcome_kind(gone) == "failed":
            receipt.setText(
                f"The old event still could not be removed: {gone}")
            return
        receipt.setText(str(gone))
        self._refresh_calendar_page()

    def _views_cal_reschedule_form(self, lay, event: dict) -> None:
        """Reschedule by event id (P3-05) — drag is not the
        mechanism. The free-slot suggestion is offered as
        "Suggested times", visually distinct from the committed
        event; picking one fills the form, it does not move the
        event until the user confirms."""
        lay.addWidget(QLabel("Move this event to a new time."))
        start = event.get("start")
        start_in = QDateTimeEdit()
        start_in.setCalendarPopup(True)
        end_in = QDateTimeEdit()
        end_in.setCalendarPopup(True)
        if isinstance(start, datetime.datetime):
            naive = start.replace(tzinfo=None)
            start_in.setDateTime(naive)
            end_in.setDateTime(naive + datetime.timedelta(hours=1))
        lay.addWidget(QLabel("New start:"))
        lay.addWidget(start_in)
        lay.addWidget(QLabel("New end:"))
        lay.addWidget(end_in)
        try:
            tzname = datetime.datetime.now().astimezone().tzname() or "local"
        except Exception:  # noqa: BLE001
            tzname = "local"
        lay.addWidget(QLabel(f"Times are local ({tzname})."))
        receipt = QLabel("")
        receipt.setObjectName("muted")
        receipt.setWordWrap(True)
        lay.addWidget(receipt)
        suggest = QPushButton("Suggest times")
        suggest.clicked.connect(lambda: self._views_cal_suggest(
            lay, event, start_in, end_in, receipt))
        lay.addWidget(suggest)
        row = QHBoxLayout()
        save = QPushButton("Move event")
        save.clicked.connect(lambda: self._views_cal_reschedule_save(
            event, start_in.dateTime().toPython(),
            end_in.dateTime().toPython(), receipt))
        row.addWidget(save)
        back = QPushButton("Back")
        back.clicked.connect(
            lambda: self._views_open_event_drawer(event, "detail"))
        row.addWidget(back)
        lay.addLayout(row)

    def _views_cal_suggest(self, lay, event, start_in, end_in,
                           receipt) -> None:
        """Suggested times for the event's day, from the real
        free/busy read — distinct from the committed event."""
        from voice_assistant.core.gui_facade import google_services
        start = event.get("start")
        day = start.date() if isinstance(start, datetime.datetime) \
            else self._views_calendar_selected_date
        duration = 60
        if isinstance(start, datetime.datetime):
            duration = 60
        try:
            text = google_services.find_free_slots(
                day.isoformat(), duration)
        except Exception as exc:  # noqa: BLE001
            receipt.setText(f"Couldn't suggest times: {exc}")
            return
        blocks = _views_parse_free_blocks(str(text))
        info = QLabel(str(text))
        info.setObjectName("muted")
        info.setWordWrap(True)
        lay.addWidget(info)
        for block_start, block_end in blocks:
            btn = QPushButton(f"Use {block_start}–{block_end}")
            btn.clicked.connect(lambda _c, s=block_start: self._views_cal_fill_slot(
                s, duration, start_in, end_in))
            lay.addWidget(btn)
        if not blocks:
            receipt.setText(str(text))

    def _views_cal_fill_slot(self, block_start, duration, start_in,
                             end_in) -> None:
        """Fill the form from a suggested block — it does not move
        the event until the user confirms."""
        try:
            hour, minute = (int(p) for p in block_start.split(":"))
            base = start_in.dateTime().toPython().replace(
                hour=hour, minute=minute, second=0, microsecond=0)
            start_in.setDateTime(base)
            end_in.setDateTime(base + datetime.timedelta(minutes=duration))
        except Exception:  # noqa: BLE001
            pass

    def _views_cal_reschedule_save(self, event, new_start, new_end,
                                   receipt) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        if new_end <= new_start:
            receipt.setText(
                "That event would end before it starts — check the "
                "start and end times.")
            return
        sentence = (f"Move '{event.get('title') or ''}' to "
                    f"{new_start.strftime('%d %b %Y, %I:%M %p').lstrip('0')}–"
                    f"{new_end.strftime('%I:%M %p').lstrip('0')}.")
        if not self._views_confirm_write(
                "reschedule_calendar_event", "Reschedule event", sentence):
            receipt.setText("Not moved — the event stays where it is.")
            return
        try:
            moved = google_services.reschedule_event(
                event_id=str(event.get("id") or ""),
                new_start=new_start.isoformat(),
                new_end=new_end.isoformat())
        except Exception as exc:  # noqa: BLE001
            receipt.setText(f"Couldn't move the event: {exc}")
            return
        if outcome_kind(moved) == "failed":
            receipt.setText(str(moved))
            return
        receipt.setText(str(moved))
        self._views_toast(str(moved))
        self._views_close_drawer(self._views_calendar_drawer,
                                 self._views_calendar_main)
        self._refresh_calendar_page()

    def _views_cal_cancel(self, event) -> None:
        """Cancel by event id — never by the fuzzy title+date
        match (P3-05). Two events with the same title on the same
        day cancel independently."""
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        sentence = (f"Cancel '{event.get('title') or ''}' on "
                    f"{(event.get('start').strftime('%d %b %Y, %I:%M %p').lstrip('0')) if isinstance(event.get('start'), datetime.datetime) else ''}.")
        if not self._views_confirm_write(
                "cancel_calendar_event", "Cancel event", sentence):
            return
        try:
            gone = google_services.cancel_event(
                event_id=str(event.get("id") or ""))
        except Exception as exc:  # noqa: BLE001
            gone = f"Couldn't cancel the event: {exc}"
        if outcome_kind(gone) == "failed":
            self._views_toast(str(gone))
            self._refresh_calendar_page()
            return
        self._views_toast(str(gone))
        self._views_close_drawer(self._views_calendar_drawer,
                                 self._views_calendar_main)
        self._refresh_calendar_page()

    def _views_open_create_drawer(self) -> None:
        """The create form in the drawer: title, start, end,
        description, location, calendar (default "primary"). No
        recurrence control — create_event has none (D-P3-04). End
        before start is refused in the form, before confirm. The
        drawer shows the timezone it will use, before the user
        confirms (P3-13)."""
        if self._views_private_on():
            return
        drawer = self._views_calendar_drawer
        self._views_drawer_clear(drawer)
        lay = drawer.content_layout()
        head = QLabel("Add event")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        title_in = QLineEdit()
        title_in.setPlaceholderText("Title")
        lay.addWidget(title_in)
        selected = self._views_calendar_selected_date
        base = datetime.datetime(selected.year, selected.month,
                                 selected.day, 10, 0)
        start_in = QDateTimeEdit(base)
        start_in.setCalendarPopup(True)
        end_in = QDateTimeEdit(base + datetime.timedelta(hours=1))
        end_in.setCalendarPopup(True)
        lay.addWidget(QLabel("Start:"))
        lay.addWidget(start_in)
        lay.addWidget(QLabel("End:"))
        lay.addWidget(end_in)
        desc_in = QLineEdit()
        desc_in.setPlaceholderText("Description (optional)")
        lay.addWidget(desc_in)
        loc_in = QLineEdit()
        loc_in.setPlaceholderText("Location (optional)")
        lay.addWidget(loc_in)
        cal_in = QLineEdit("primary")
        cal_in.setPlaceholderText("Calendar (primary)")
        lay.addWidget(QLabel("Calendar:"))
        lay.addWidget(cal_in)
        try:
            tzname = datetime.datetime.now().astimezone().tzname() or "local"
        except Exception:  # noqa: BLE001
            tzname = "local"
        tz = QLabel(f"Times are local ({tzname}). A timezone offset "
                    "in the time is honoured; a naive time is read as local.")
        tz.setObjectName("muted")
        tz.setWordWrap(True)
        lay.addWidget(tz)
        receipt = QLabel("")
        receipt.setObjectName("muted")
        receipt.setWordWrap(True)
        lay.addWidget(receipt)
        suggest = QPushButton("Suggest times")
        suggest.clicked.connect(lambda: self._views_cal_suggest_create(
            lay, start_in, end_in, receipt))
        lay.addWidget(suggest)
        row = QHBoxLayout()
        create = QPushButton("Create event")
        create.clicked.connect(lambda: self._views_cal_create_save(
            title_in.text(), start_in.dateTime().toPython(),
            end_in.dateTime().toPython(), desc_in.text(),
            loc_in.text(), cal_in.text(), receipt))
        row.addWidget(create)
        close = QPushButton("Close")
        close.clicked.connect(lambda: self._views_close_drawer(
            drawer, self._views_calendar_main))
        row.addWidget(close)
        lay.addLayout(row)
        lay.addStretch(1)
        self._views_open_drawer(drawer, self._views_calendar_main)

    def _views_cal_suggest_create(self, lay, start_in, end_in,
                                  receipt) -> None:
        from voice_assistant.core.gui_facade import google_services
        day = start_in.dateTime().toPython().date()
        try:
            duration = max(
                5, int((end_in.dateTime().toPython()
                        - start_in.dateTime().toPython()).total_seconds()
                       // 60))
        except Exception:  # noqa: BLE001
            duration = 60
        try:
            text = google_services.find_free_slots(
                day.isoformat(), duration)
        except Exception as exc:  # noqa: BLE001
            receipt.setText(f"Couldn't suggest times: {exc}")
            return
        blocks = _views_parse_free_blocks(str(text))
        info = QLabel(str(text))
        info.setObjectName("muted")
        info.setWordWrap(True)
        lay.addWidget(info)
        for block_start, block_end in blocks:
            btn = QPushButton(f"Use {block_start}–{block_end}")
            btn.clicked.connect(lambda _c, s=block_start: self._views_cal_fill_slot(
                s, duration, start_in, end_in))
            lay.addWidget(btn)
        if not blocks:
            receipt.setText(str(text))

    def _views_cal_create_save(self, title, start, end, description,
                               location, calendar_id, receipt) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        title = (title or "").strip()
        if not title:
            receipt.setText("The event needs a title.")
            return
        if end <= start:
            receipt.setText(
                "That event would end before it starts — check the "
                "start and end times.")
            return
        sentence = (f"Create event '{title}' on "
                    f"{start.strftime('%d %b %Y, %I:%M %p').lstrip('0')}–"
                    f"{end.strftime('%I:%M %p').lstrip('0')}"
                    + (f" at {location.strip()}" if (location or "").strip() else "")
                    + ".")
        if not self._views_confirm_write(
                "create_calendar_event", "Create event", sentence):
            receipt.setText("Not created.")
            return
        try:
            created = google_services.create_event(
                title, start.isoformat(), end.isoformat(),
                description=(description or "").strip(),
                location=(location or "").strip(),
                calendar_id=(calendar_id or "primary").strip() or "primary")
        except Exception as exc:  # noqa: BLE001
            receipt.setText(f"Couldn't create the event: {exc}")
            return
        if outcome_kind(created) == "failed":
            receipt.setText(str(created))
            return
        receipt.setText(str(created))
        self._views_toast(str(created))
        self._views_close_drawer(self._views_calendar_drawer,
                                 self._views_calendar_main)
        self._refresh_calendar_page()

    # ------------------------------------------------------------------
    # Inbox (index 9)
    # ------------------------------------------------------------------
    def _build_inbox_page(self) -> QWidget:
        page, content = self._page_shell(
            "Inbox",
            "Your newest mail — pick one to read it.", kicker=True,
        )
        main = QWidget()
        main_lay = QVBoxLayout(main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(10)
        self._views_inbox_main = main
        # Header: search + Compose. The search cap is stated on
        # the page (P3-17): search_email returns at most 6.
        header = QHBoxLayout()
        header.setSpacing(8)
        self._views_inbox_search = QLineEdit()
        self._views_inbox_search.setPlaceholderText("Search mail")
        self._views_inbox_search.returnPressed.connect(
            self._views_inbox_search_now)
        header.addWidget(self._views_inbox_search, 1)
        search_btn = QPushButton("Search")
        search_btn.clicked.connect(self._views_inbox_search_now)
        header.addWidget(search_btn)
        self._views_inbox_compose = QPushButton("Compose")
        self._views_inbox_compose.clicked.connect(
            lambda: self._views_open_compose_drawer("compose", None))
        header.addWidget(self._views_inbox_compose)
        main_lay.addLayout(header)

        tabs_row = QHBoxLayout()
        tabs_row.setSpacing(8)
        self._views_inbox_tabs = HudTabs()
        for label in ("All", "Unread", "Snoozed"):
            self._views_inbox_tabs.addTab(label)
        self._views_inbox_tabs.currentChanged.connect(
            self._views_inbox_tab_changed)
        tabs_row.addWidget(self._views_inbox_tabs)
        tabs_row.addStretch(1)
        self._views_inbox_unread_label = QLabel("")
        self._views_inbox_unread_label.setObjectName("muted")
        tabs_row.addWidget(self._views_inbox_unread_label)
        main_lay.addLayout(tabs_row)

        sync_row = QHBoxLayout()
        sync_row.setSpacing(8)
        self._views_inbox_sync = QLabel("")
        self._views_inbox_sync.setObjectName("muted")
        self._views_inbox_sync.setWordWrap(True)
        sync_row.addWidget(self._views_inbox_sync, 1)
        self._views_inbox_connect = self._views_connect_button()
        self._views_inbox_connect.hide()
        sync_row.addWidget(self._views_inbox_connect)
        main_lay.addLayout(sync_row)

        self._views_inbox_results_label = QLabel("")
        self._views_inbox_results_label.setObjectName("muted")
        main_lay.addWidget(self._views_inbox_results_label)

        split = QHBoxLayout()
        split.setSpacing(12)
        list_col = QVBoxLayout()
        list_col.setSpacing(8)
        self._views_inbox_list = QListWidget()
        # No wrapping: each row is two fixed lines (who/what,
        # then when/snippet) with the snippet elided — the rows
        # used to wrap mid-sentence with the time inline in the
        # middle of the title line.
        self._views_inbox_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._views_inbox_list.itemActivated.connect(
            self._on_views_inbox_picked)
        list_col.addWidget(self._views_inbox_list, 1)
        self._views_inbox_empty = EmptyState(
            sentence="No mail to show.",
            suggested_task="New mail will appear in the list.",
            action_label="Clear search")
        self._views_inbox_empty.action_requested.connect(
            self._views_inbox_clear_search)
        self._views_inbox_empty.hide()
        list_col.addWidget(self._views_inbox_empty)
        split.addLayout(list_col, 1)

        pane_col = QVBoxLayout()
        pane_col.setSpacing(8)
        self._views_inbox_body = QTextBrowser()
        self._views_inbox_body.setOpenExternalLinks(False)
        self._views_inbox_body.setPlainText(
            "Pick a message from the list to read it.")
        pane_col.addWidget(self._views_inbox_body, 1)
        self._views_inbox_summary_btn = QPushButton(
            "Read the full message")
        self._views_inbox_summary_btn.hide()
        self._views_inbox_summary_btn.clicked.connect(
            self._views_inbox_summary_toggle)
        pane_col.addWidget(self._views_inbox_summary_btn)
        self._views_inbox_attach_label = QLabel("")
        self._views_inbox_attach_label.setObjectName("muted")
        self._views_inbox_attach_label.setWordWrap(True)
        self._views_inbox_attach_label.hide()
        pane_col.addWidget(self._views_inbox_attach_label)
        self._views_inbox_attach_row = QHBoxLayout()
        self._views_inbox_attach_row.setSpacing(6)
        pane_col.addLayout(self._views_inbox_attach_row)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._views_inbox_reply = QPushButton("Reply")
        self._views_inbox_reply.clicked.connect(
            self._views_inbox_reply_now)
        actions.addWidget(self._views_inbox_reply)
        self._views_inbox_snooze = QPushButton("Snooze")
        self._views_inbox_snooze.clicked.connect(
            self._views_inbox_snooze_now)
        actions.addWidget(self._views_inbox_snooze)
        actions.addStretch(1)
        pane_col.addLayout(actions)
        split.addLayout(pane_col, 2)
        main_lay.addLayout(split, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._views_inbox_cap = QLabel(
            f"Showing the latest {_INBOX_LIST_LIMIT} messages.")
        self._views_inbox_cap.setObjectName("muted")
        footer.addWidget(self._views_inbox_cap)
        footer.addStretch(1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_inbox_page)
        footer.addWidget(refresh)
        main_lay.addLayout(footer)

        self._views_inbox_drawer = ContextDrawer(title="Compose")
        self._views_inbox_drawer.setObjectName("viewsInboxDrawer")
        self._views_inbox_drawer.hide()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(main, 1)
        row.addWidget(self._views_inbox_drawer)
        content.addLayout(row)
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "surface_1": SURFACE_1,
            "line_subtle": LINE_SUBTLE, "line_strong": LINE_STRONG,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        self._views_inbox_messages: list[dict] = []
        self._views_inbox_unread_count = None
        self._views_inbox_unread_ids: set = set()
        self._views_inbox_tab = "All"
        self._views_inbox_search_results = None
        self._views_inbox_search_query = ""
        self._views_inbox_selected_id = ""
        self._views_inbox_selected_kind = ""
        self._views_inbox_current = None
        self._views_inbox_summary_shown = False
        self._views_inbox_can_write = True
        self._views_compose_draft = None
        return page

    def _refresh_inbox_page(self) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import (
            unread_count as _facade_unread_count)

        if getattr(self, "_views_inbox_list", None) is None:
            return
        try:
            scroll = self._views_inbox_list.verticalScrollBar().value()
        except Exception:  # noqa: BLE001
            scroll = 0
        try:
            connected = bool(google_services.is_connected())
        except Exception:  # noqa: BLE001
            connected = False
        self._views_inbox_can_write = (
            self._views_can_write("gmail") if connected else False)
        private = self._views_private_on()
        if not connected:
            # Hunt fix (c) item 5: the connect line lives ONLY in
            # the header sync slot above (with the Connect
            # button). The list and reading panes get their own
            # empty states — the old code repeated the line in
            # both (3× total, the list item clipped). Follows the
            # Contacts/Calls pattern.
            self._views_inbox_sync.setText(_CONNECT_GOOGLE)
            self._views_inbox_connect.show()
            self._views_inbox_compose.setVisible(False)
            self._views_inbox_unread_count = None
            self._views_inbox_list.clear()
            self._views_inbox_empty_show("No mail to show.", False)
            self._views_inbox_body.setPlainText(
                "Pick a message from the list to read it.")
            self._views_inbox_unread_label.setText("")
            return
        self._views_inbox_connect.hide()
        if not self._views_inbox_can_write:
            self._views_inbox_sync.setText(
                self._views_scope_sentence("sending mail"))
            self._views_inbox_compose.setVisible(False)
        else:
            self._views_inbox_sync.setText(
                "Connected — Private mode is on. Senders and "
                "subjects are hidden." if private else "Connected.")
            self._views_inbox_compose.setVisible(not private)
        # One shared unread count (D-P3-11): the facade's
        # unread_count feeds this page, the sidebar badge (via the
        # cache below) and the Home strip. A failed read clears
        # the cache instead of leaving a stale count.
        try:
            self._views_inbox_unread_count = int(
                _facade_unread_count(200))
        except Exception:  # noqa: BLE001
            self._views_inbox_unread_count = None
        if self._views_inbox_unread_count is None:
            self._views_inbox_unread_label.setText("")
        else:
            self._views_inbox_unread_label.setText(
                f"{self._views_inbox_unread_count} unread")
        try:
            messages = google_services.recent_messages(_INBOX_LIST_LIMIT)
        except Exception:  # noqa: BLE001
            messages = []
        self._views_inbox_messages = messages or []
        self._views_inbox_unread_ids = self._views_inbox_unread_id_set()
        self._views_fill_inbox_list()
        try:
            self._views_inbox_list.verticalScrollBar().setValue(scroll)
        except Exception:  # noqa: BLE001
            pass
        # The selection survives the refresh (D-P3-18): re-show
        # the selected message (or snoozed record) so the pane's
        # buttons match what is on screen. With nothing selected
        # yet, open the first (preferably unread) message so the
        # pane shows real mail on open instead of one line.
        try:
            if self._views_inbox_selected_id:
                if self._views_inbox_selected_kind == "snoozed":
                    self._views_inbox_show_snoozed(
                        self._views_inbox_selected_id)
                else:
                    self._views_inbox_show_message(
                        self._views_inbox_selected_id)
        except Exception:  # noqa: BLE001
            pass
        self._views_inbox_auto_pick()

    def _views_inbox_unread_id_set(self) -> set:
        """The unread message ids, from the provider's is:unread
        query — recent_messages carries no read flag, so this is
        the only per-message unread seam. Parsed exactly from the
        search prose (the [id: …] markers); a failed read yields
        no markers, never guessed ones."""
        try:
            from voice_assistant.core.gui_facade import google_services
            prose = google_services.search_email(
                "is:unread", limit=_UNREAD_SEARCH_LIMIT)
            return {row["id"] for row in
                    _views_parse_search_results(str(prose))}
        except Exception:  # noqa: BLE001
            return set()

    def _views_inbox_empty_show(self, sentence: str, clearable: bool) -> None:
        """The list-area empty state. The Clear search action
        shows only for a search no-match — the other empty states
        have nothing to clear."""
        self._views_inbox_list.hide()
        self._views_inbox_empty.set_sentence(sentence)
        self._views_inbox_cap.setText("Showing 0 messages.")
        button = getattr(self._views_inbox_empty, "_button", None)
        if button is not None:
            button.setVisible(bool(clearable))
        self._views_inbox_empty.show()

    def _views_fill_inbox_list(self) -> None:
        listing = self._views_inbox_list
        listing.clear()
        self._views_inbox_empty.hide()
        listing.show()
        private = self._views_private_on()
        tab = self._views_inbox_tab
        if self._views_inbox_search_results is not None:
            results = self._views_inbox_search_results
            self._views_inbox_results_label.setText(
                f"Top {_SEARCH_LIMIT} matches for "
                f"'{self._views_inbox_search_query}'")
            if not results:
                self._views_inbox_empty_show(
                    f"No emails found for "
                    f"'{self._views_inbox_search_query}'.", True)
                return
            for message in results:
                listing.addItem(
                    self._views_inbox_row(message, private))
            self._views_inbox_set_cap()
            return
        self._views_inbox_results_label.setText("")
        if tab == "Snoozed":
            rows = self._views_snoozed_rows()
            if not rows:
                self._views_inbox_empty_show(
                    "Nothing snoozed — snoozed mail will appear here.",
                    False)
                return
            for row in rows:
                item = QListWidgetItem(
                    f"{_views_elide(row.get('sender') or 'Unknown sender', 40)} — "
                    f"{_views_elide(row.get('subject') or '(no subject)', 40)}\n"
                    f"Snoozed until {row.get('until_text') or ''}")
                item.setData(Qt.ItemDataRole.UserRole,
                             f"snoozed:{row.get('message_id') or ''}")
                listing.addItem(item)
            self._views_inbox_set_cap()
            return
        messages = self._views_inbox_messages
        if tab == "Unread":
            messages = [m for m in messages
                        if str(m.get("id") or "")
                        in self._views_inbox_unread_ids]
        if not messages:
            self._views_inbox_empty_show(
                "No unread mail." if tab == "Unread"
                else "No mail to show.", False)
            return
        for message in messages:
            listing.addItem(self._views_inbox_row(message, private))
        self._views_inbox_set_cap()

    def _views_inbox_set_cap(self) -> None:
        """The footer counts what is actually listed — it used to
        say "Showing the latest 20 messages" while three were
        listed. The search results label already carries the
        query; this line carries the count."""
        n = self._views_inbox_list.count()
        self._views_inbox_cap.setText(
            f"Showing {n} message{'s' if n != 1 else ''}.")

    def _views_inbox_auto_pick(self) -> None:
        """Open the first (preferably unread) listed message so
        the reading pane shows real mail on open — the pane used
        to sit empty with one line ("Pick a message…") until the
        user clicked. Only when nothing is selected yet; a
        selection survives refreshes (D-P3-18)."""
        listing = self._views_inbox_list
        if listing.count() == 0 or self._views_inbox_selected_id:
            return
        pick = 0
        for i in range(listing.count()):
            ref = str(listing.item(i).data(Qt.ItemDataRole.UserRole)
                      or "")
            mid = (ref[len("snoozed:"):]
                   if ref.startswith("snoozed:") else ref)
            if mid and mid in self._views_inbox_unread_ids:
                pick = i
                break
        listing.setCurrentRow(pick)
        self._on_views_inbox_picked(listing.item(pick))

    def _views_inbox_row(self, message: dict, private: bool) -> QListWidgetItem:
        """One list row, two fixed lines. Line 1 is who and what;
        line 2 is when and a snippet elided at a word boundary.
        The time sits in one consistent place (the start of line
        2), never inline in the title, and the snippet truncates
        with an ellipsis instead of wrapping mid-sentence. The
        sender shows a name, never a bare address. In private
        mode the sender, subject and snippet are blanked and the
        count stays. Unread rows carry the unread marker — only
        where the backend says so (the unread id set); the page
        never invents read state."""
        if private:
            item = QListWidgetItem("Private mode is on")
            item.setData(Qt.ItemDataRole.UserRole,
                         message.get("id") or "")
            return item
        unread = str(message.get("id") or "") in self._views_inbox_unread_ids
        prefix = "● " if unread else ""
        sender = _views_sender_name(
            message.get("sender") or "Unknown sender")
        subject = _views_elide(
            message.get("subject") or "(no subject)", 40)
        when = message.get("when") or ""
        snippet = _views_elide(message.get("snippet") or "", 48)
        second = f"{when} · {snippet}" if when else snippet
        item = QListWidgetItem(
            f"{prefix}{sender} — {subject}\n{second}" if second
            else f"{prefix}{sender} — {subject}")
        if unread:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        item.setData(Qt.ItemDataRole.UserRole, message.get("id") or "")
        return item

    def _views_snoozed_rows(self) -> list[dict]:
        """Snoozed mail from the local store: live snoozes only.
        recent_messages hides snoozed mail from All, so the
        Snoozed tab is where snoozed mail lives — no "Snoozed
        until …" marker can appear on All rows."""
        try:
            from voice_assistant.core.gui_facade import google_services
            rows = google_services._load_snoozed()
            active = google_services._active_snooze_ids()
        except Exception:  # noqa: BLE001
            return []
        out = []
        for row in rows or []:
            message_id = str(row.get("message_id") or "")
            if message_id not in active:
                continue
            until = str(row.get("snooze_until") or "")
            try:
                parsed = google_services._parse_snooze_until(until)
                until_text = parsed.strftime(
                    "%a %d %b %H:%M") if parsed else until
            except Exception:  # noqa: BLE001
                until_text = until
            out.append({"message_id": message_id,
                        "sender": _views_sender_name(
                            row.get("sender") or "Unknown sender"),
                        "subject": row.get("subject") or "(no subject)",
                        "until_text": until_text})
        return out

    def _views_inbox_tab_changed(self, index: int) -> None:
        self._views_inbox_tab = ("All", "Unread", "Snoozed")[index]
        self._views_inbox_search_results = None
        self._views_inbox_search.clear()
        self._views_fill_inbox_list()
        # A selection that is not in this tab's rows is stale —
        # open the tab's first (preferably unread) message
        # instead of leaving the pane on mail the list hides.
        refs = {str(self._views_inbox_list.item(i).data(
            Qt.ItemDataRole.UserRole) or "")
            for i in range(self._views_inbox_list.count())}
        selected = self._views_inbox_selected_id
        if selected and selected not in refs \
                and f"snoozed:{selected}" not in refs:
            self._views_inbox_selected_id = ""
            self._views_inbox_selected_kind = ""
        self._views_inbox_auto_pick()

    def _on_views_inbox_picked(self, item: QListWidgetItem) -> None:
        ref = item.data(Qt.ItemDataRole.UserRole) or ""
        if not ref:
            return
        if str(ref).startswith("snoozed:"):
            self._views_inbox_show_snoozed(str(ref)[len("snoozed:"):])
            return
        self._views_inbox_show_message(str(ref))

    def _views_inbox_show_snoozed(self, message_id: str) -> None:
        row = next((r for r in self._views_snoozed_rows()
                    if r.get("message_id") == message_id), None)
        self._views_inbox_selected_id = message_id
        self._views_inbox_selected_kind = "snoozed"
        if row is None:
            self._views_inbox_body.setPlainText(
                "That snooze is no longer active.")
            return
        self._views_inbox_current = None
        self._views_inbox_body.setPlainText(
            f"{row.get('subject')}\nFrom: {row.get('sender')}\n"
            f"Snoozed until {row.get('until_text')}.")
        self._views_inbox_summary_btn.hide()
        self._views_inbox_attach_label.hide()
        self._views_clear_attach_row()
        self._views_inbox_reply.hide()
        self._views_inbox_snooze.setText("Bring back now")
        self._views_inbox_snooze.show()
        try:
            self._views_inbox_snooze.clicked.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._views_inbox_snooze.clicked.connect(
            self._views_inbox_bring_back_now)

    def _views_inbox_bring_back_now(self) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        message_id = self._views_inbox_selected_id
        if not message_id:
            return
        try:
            result = google_services.snooze_email(message_id)
        except Exception as exc:  # noqa: BLE001
            result = f"Couldn't bring that mail back: {exc}"
        if outcome_kind(result) == "failed":
            self._views_toast(str(result))
            return
        self._views_toast(str(result))
        self._refresh_inbox_page()

    def _views_inbox_show_message(self, message_id: str) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import summarise_text
        self._views_inbox_selected_id = message_id
        self._views_inbox_selected_kind = "message"
        self._views_inbox_snooze.setText("Snooze")
        try:
            self._views_inbox_snooze.clicked.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self._views_inbox_snooze.clicked.connect(
            self._views_inbox_snooze_now)
        private = self._views_private_on()
        if private:
            self._views_inbox_body.setPlainText(
                "Private mode is on — the sender, subject and body "
                "are hidden.")
            self._views_inbox_summary_btn.hide()
            self._views_inbox_attach_label.hide()
            self._views_clear_attach_row()
            self._views_inbox_reply.hide()
            self._views_inbox_snooze.hide()
            return
        message = next((m for m in self._views_inbox_messages
                        if str(m.get("id") or "") == message_id), None)
        if message is None:
            message = next((m for m in
                            (self._views_inbox_search_results or [])
                            if str(m.get("id") or "") == message_id), None)
        try:
            text = google_services.message_body(message_id)
        except Exception:  # noqa: BLE001
            text = ""
        body = text or "(Nothing readable in this message.)"
        summary = ""
        try:
            result = summarise_text(body, max_sentences=2)
            summary = (result or {}).get("summary") or ""
        except Exception:  # noqa: BLE001 — a failed summary
            summary = ""  # shows the body, never a canned line.
        self._views_inbox_current = {
            "id": message_id,
            "sender": (message or {}).get("sender") or "Unknown sender",
            "subject": (message or {}).get("subject") or "(no subject)",
            "when": (message or {}).get("when") or "",
            "body": body, "summary": summary}
        # The Summary line is shown first (labelled "Summary");
        # the original is one tap away. A short body has no
        # summary — the body is shown directly.
        if summary and summary.strip() != body.strip():
            self._views_inbox_summary_shown = True
            self._views_inbox_body.setPlainText(f"Summary\n{summary}")
            self._views_inbox_summary_btn.setText("Read the full message")
            self._views_inbox_summary_btn.show()
        else:
            self._views_inbox_summary_shown = False
            self._views_inbox_body.setPlainText(body)
            self._views_inbox_summary_btn.hide()
        self._views_inbox_show_attachments(message_id)
        can_write = self._views_inbox_can_write and not private
        self._views_inbox_reply.setVisible(can_write)
        self._views_inbox_snooze.setVisible(True)

    def _views_inbox_summary_toggle(self) -> None:
        current = self._views_inbox_current or {}
        if self._views_inbox_summary_shown:
            self._views_inbox_body.setPlainText(
                current.get("body") or "")
            self._views_inbox_summary_btn.setText("Show summary")
            self._views_inbox_summary_shown = False
        else:
            self._views_inbox_body.setPlainText(
                f"Summary\n{current.get('summary') or ''}")
            self._views_inbox_summary_btn.setText("Read the full message")
            self._views_inbox_summary_shown = True

    def _views_clear_attach_row(self) -> None:
        # Hold the widget in a local: calling child.widget()
        # fresh for the guard, the reparent and the delete drops
        # each temporary wrapper in turn, and the reparent
        # orphans the button — with no Python reference left,
        # the wrapper's destruction deletes the C++ widget, so
        # the next child.widget() reads None and the clear
        # crashes (the snooze test caught it: viewing a message
        # with attachments, then picking its snoozed row).
        row = self._views_inbox_attach_row
        while row.count():
            child = row.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _views_message_attachments(self, message_id: str) -> list[dict]:
        """A message's attachments as {filename, size} — from the
        listing the backend returns for a filename that matches
        nothing. That call downloads nothing: it is one metadata
        read, and the listing is parsed exactly ('name' (size)
        pairs). An unparseable listing yields no rows — the pane
        never shows a row for a part that failed to decode."""
        try:
            from voice_assistant.core.gui_facade import google_services
            text = google_services.download_attachment(
                message_id, filename=_ATTACH_LISTING_PROBE)
        except Exception:  # noqa: BLE001
            return []
        if "doesn't have any attachments" in str(text):
            return []
        return [{"filename": m.group(1), "size": m.group(2)}
                for m in re.finditer(
                    r"'([^']+)' \(([^)]+)\)", str(text))]

    def _views_inbox_show_attachments(self, message_id: str) -> None:
        attachments = self._views_message_attachments(message_id)
        self._views_clear_attach_row()
        label = self._views_inbox_attach_label
        if not attachments:
            label.hide()
            return
        label.setText("Attachments: " + ", ".join(
            f"'{a['filename']}' ({a['size']})" for a in attachments))
        label.show()
        for attachment in attachments:
            btn = QPushButton(f"Download {attachment['filename']}")
            btn.clicked.connect(
                lambda _c, name=attachment["filename"]:
                self._views_inbox_download_attachment(
                    message_id, name))
            self._views_inbox_attach_row.addWidget(btn)
        self._views_inbox_attach_row.addStretch(1)

    def _views_inbox_download_attachment(self, message_id, filename) -> None:
        from voice_assistant.core.gui_facade import google_services
        try:
            result = google_services.download_attachment(
                message_id, filename=filename)
        except Exception as exc:  # noqa: BLE001
            result = f"Couldn't download that file: {exc}"
        self._views_toast(str(result))

    def _views_inbox_reply_now(self) -> None:
        current = self._views_inbox_current
        if not current:
            return
        self._views_open_compose_drawer("reply", current)

    def _views_inbox_snooze_now(self) -> None:
        message_id = self._views_inbox_selected_id
        if not message_id:
            return
        dialog = _ViewsSnoozeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        try:
            result = google_services.snooze_email(
                message_id, until=dialog.until)
        except Exception as exc:  # noqa: BLE001
            result = f"Couldn't snooze that mail: {exc}"
        if outcome_kind(result) == "failed":
            self._views_toast(str(result))
            return
        self._views_toast(str(result))
        self._refresh_inbox_page()

    def _views_inbox_search_now(self) -> None:
        from voice_assistant.core.gui_facade import google_services
        query = (self._views_inbox_search.text() or "").strip()
        if not query:
            self._views_inbox_clear_search()
            return
        self._views_inbox_search_query = query
        try:
            prose = google_services.search_email(query)
        except Exception as exc:  # noqa: BLE001
            prose = f"Couldn't search your mail just now ({exc})."
        if str(prose).startswith("No emails found"):
            self._views_inbox_search_results = []
        else:
            self._views_inbox_search_results = \
                _views_parse_search_results(str(prose))
        self._views_fill_inbox_list()

    def _views_inbox_clear_search(self) -> None:
        self._views_inbox_search_results = None
        self._views_inbox_search.clear()
        self._views_fill_inbox_list()


    # -- Inbox compose / reply (one drawer, D-P3-06) --------------
    def _views_open_compose_drawer(self, mode: str,
                                   current: dict | None,
                                   prefill: dict | None = None) -> None:
        """Compose (empty) or Reply (prefilled) in the one drawer.

        The draft is local and in memory only — never auto-sent,
        never auto-saved to the server, no persistence promised
        (S-P3-06). It is labelled "Draft — not saved" in the
        drawer. The draft VALUES live in _views_compose_values so
        Back-to-edit and Retry rebuild the form with the user's
        words intact. Reply prefills To with the sender's address
        ONLY when the inbox actually carries one (a bare address);
        a display name is never treated as an address, and the
        drawer says so. Threading is not offered: no thread_id /
        in_reply_to seam reaches the GUI, so a reply sends as a
        new message and the drawer says so.
        """
        if self._views_private_on():
            return
        drawer = self._views_inbox_drawer
        self._views_drawer_clear(drawer)
        lay = drawer.content_layout()
        head = QLabel("Reply" if mode == "reply" else "Compose")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        draft_note = QLabel("Draft — not saved")
        draft_note.setObjectName("muted")
        lay.addWidget(draft_note)
        to_in = QLineEdit()
        to_in.setPlaceholderText("To")
        subject_in = QLineEdit()
        subject_in.setPlaceholderText("Subject")
        body_in = QTextEdit()
        body_in.setPlaceholderText("Write your message…")
        if prefill:
            to_in.setText(prefill.get("to") or "")
            subject_in.setText(prefill.get("subject") or "")
            body_in.setPlainText(prefill.get("body") or "")
        elif mode == "reply" and current:
            address = _views_sender_address(current.get("sender") or "")
            to_in.setText(address)
            subject = current.get("subject") or ""
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            subject_in.setText(subject)
            quoted = (f"\n\n— — —\nOn {current.get('when') or ''}, "
                      f"{current.get('sender') or ''} wrote:\n"
                      f"{current.get('body') or ''}")
            body_in.setPlainText(quoted)
            if not address:
                note = QLabel(
                    "The sender's address isn't shown — the inbox "
                    "shows names, not addresses. Type the address "
                    "to send to.")
                note.setObjectName("muted")
                note.setWordWrap(True)
                lay.addWidget(note)
            thread_note = QLabel(
                "Sent as a new message — threading isn't available "
                "from the inbox.")
            thread_note.setObjectName("muted")
            thread_note.setWordWrap(True)
            lay.addWidget(thread_note)
        lay.addWidget(QLabel("To:"))
        lay.addWidget(to_in)
        lay.addWidget(QLabel("Subject:"))
        lay.addWidget(subject_in)
        lay.addWidget(QLabel("Body:"))
        lay.addWidget(body_in, 1)
        attach_list = QListWidget()
        attach_list.setMaximumHeight(80)
        if prefill:
            for path in prefill.get("attachments") or []:
                attach_list.addItem(path)
        lay.addWidget(QLabel("Attachments (at most 5, at most 25 MB each):"))
        lay.addWidget(attach_list)
        receipt = QLabel("")
        receipt.setObjectName("muted")
        receipt.setWordWrap(True)
        lay.addWidget(receipt)
        attach_row = QHBoxLayout()
        attach_row.setSpacing(6)
        add_btn = QPushButton("Add attachment")
        add_btn.clicked.connect(lambda: self._views_compose_add_file(
            attach_list, receipt))
        attach_row.addWidget(add_btn)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(
            lambda: self._views_compose_remove_file(attach_list))
        attach_row.addWidget(remove_btn)
        attach_row.addStretch(1)
        lay.addLayout(attach_row)
        self._views_compose_draft = {
            "to": to_in, "subject": subject_in, "body": body_in,
            "attach_list": attach_list, "receipt": receipt,
            "mode": mode,
            "current": current}
        row = QHBoxLayout()
        send = QPushButton("Review and send")
        send.clicked.connect(self._views_compose_show_review)
        row.addWidget(send)
        discard = QPushButton("Discard draft")
        discard.clicked.connect(self._views_compose_discard)
        row.addWidget(discard)
        lay.addLayout(row)
        lay.addStretch(1)
        self._views_open_drawer(drawer, self._views_inbox_main)

    def _views_compose_values(self) -> dict:
        """The draft values — from the live form widgets when the
        form is open, else from the last saved values (the review
        rebuilds the form, so the values must survive it)."""
        draft = self._views_compose_draft or {}
        to_in = draft.get("to")
        if to_in is not None:
            values = {
                "to": to_in.text().strip(),
                "subject": (draft.get("subject").text().strip()
                            if draft.get("subject") is not None else ""),
                "body": (draft.get("body").toPlainText()
                         if draft.get("body") is not None else ""),
                "attachments": self._views_compose_attachments(),
                "mode": draft.get("mode") or "compose",
                "current": draft.get("current")}
            self._views_compose_saved = values
            return values
        return getattr(self, "_views_compose_saved", {
            "to": "", "subject": "", "body": "", "attachments": [],
            "mode": "compose", "current": None})

    def _views_compose_attachments(self) -> list[str]:
        draft = self._views_compose_draft or {}
        listing = draft.get("attach_list")
        if listing is None:
            saved = getattr(self, "_views_compose_saved", None)
            return list((saved or {}).get("attachments") or [])
        return [listing.item(i).text()
                for i in range(listing.count())]

    def _views_compose_add_file(self, attach_list, receipt) -> None:
        """Add attachments, enforcing the caps in the drawer before
        confirm (P3-15): at most 5, at most 25 MB each; a missing
        or unreadable file refuses the send in the drawer, before
        the approval card is ever shown."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add attachments")
        for path in paths or []:
            if attach_list.count() >= _ATTACH_MAX_FILES:
                receipt.setText(
                    "At most 5 attachments per email — remove one "
                    "to add another.")
                return
            if not os.path.isfile(path):
                receipt.setText(f"No such file: {path}")
                return
            try:
                size = os.path.getsize(path)
            except OSError:
                receipt.setText(f"Couldn't read that file: {path}")
                return
            if size > _ATTACH_MAX_BYTES:
                receipt.setText(
                    f"'{os.path.basename(path)}' is larger than "
                    "Gmail's 25 MB attachment ceiling.")
                return
            attach_list.addItem(path)
        receipt.setText("")

    def _views_compose_remove_file(self, attach_list) -> None:
        row = attach_list.currentRow()
        if row >= 0:
            attach_list.takeItem(row)

    def _views_compose_discard(self) -> None:
        """Closing the drawer discards the draft after a confirm
        ("Discard this draft?") — the discard is the only
        data-destructive action in the drawer and it is the user's
        own draft, so the confirm is a plain dialog, not the 60 s
        approval card."""
        values = self._views_compose_values()
        has_content = bool(values.get("to") or values.get("subject")
                           or (values.get("body") or "").strip()
                           or values.get("attachments"))
        if has_content:
            answer = QMessageBox.question(
                self, "Discard this draft?",
                "Discard this draft? It is not saved anywhere.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._views_compose_draft = None
        self._views_compose_saved = None
        self._views_close_drawer(self._views_inbox_drawer,
                                 self._views_inbox_main)

    def _views_compose_show_review(self) -> None:
        """Pre-send review of the FINAL payload (P3-02): the exact
        To / Subject / Body / attachments, as they will be sent.
        Confirming goes through the approval contract and then
        send_email; the review shows the payload, never a summary
        of it."""
        values = self._views_compose_values()
        to = (values.get("to") or "").strip()
        subject = (values.get("subject") or "").strip()
        body = values.get("body") or ""
        attachments = list(values.get("attachments") or [])
        draft = self._views_compose_draft or {}
        receipt = draft.get("receipt")
        if not to:
            if receipt is not None:
                receipt.setText("Add a recipient first.")
            return
        if not body.strip():
            if receipt is not None:
                receipt.setText("Write the message first.")
            return
        # Re-enforce the caps at review time (the files could
        # have changed since they were added).
        for path in attachments:
            if not os.path.isfile(path):
                if receipt is not None:
                    receipt.setText(f"No such file: {path}")
                return
            try:
                size = os.path.getsize(path)
            except OSError:
                if receipt is not None:
                    receipt.setText(f"Couldn't read that file: {path}")
                return
            if size > _ATTACH_MAX_BYTES:
                if receipt is not None:
                    receipt.setText(
                        f"'{os.path.basename(path)}' is larger than "
                        "Gmail's 25 MB attachment ceiling.")
                return
        drawer = self._views_inbox_drawer
        self._views_drawer_clear(drawer)
        # The form widgets are gone now — the draft reference must
        # go with them (a deleted widget raises on access), so the
        # values fall back to the saved copy above.
        self._views_compose_draft = None
        lay = drawer.content_layout()
        head = QLabel("Review before sending")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        for label, value in (("To:", to), ("Subject:", subject),
                             ("Body:", body)):
            key = QLabel(label)
            key.setObjectName("muted")
            lay.addWidget(key)
            val = QLabel(value)
            val.setWordWrap(True)
            lay.addWidget(val)
        key = QLabel("Attachments:")
        key.setObjectName("muted")
        lay.addWidget(key)
        lay.addWidget(QLabel(
            ", ".join(os.path.basename(p) for p in attachments)
            if attachments else "None"))
        review_receipt = QLabel("")
        review_receipt.setObjectName("muted")
        review_receipt.setWordWrap(True)
        lay.addWidget(review_receipt)
        row = QHBoxLayout()
        confirm = QPushButton("Send")
        confirm.clicked.connect(lambda: self._views_compose_send(
            to, subject, body, attachments, review_receipt))
        row.addWidget(confirm)
        back = QPushButton("Back to edit")
        back.clicked.connect(lambda: self._views_open_compose_drawer(
            values.get("mode") or "compose", values.get("current"),
            prefill=values))
        row.addWidget(back)
        lay.addLayout(row)
        lay.addStretch(1)

    def _views_compose_send(self, to, subject, body, attachments,
                            receipt) -> None:
        from voice_assistant.core.gui_facade import google_services
        from voice_assistant.core.gui_facade import outcome_kind
        names = ", ".join(os.path.basename(p) for p in attachments)
        sentence = (f"Send email to {to} — subject '{subject}'"
                    + (f" — attachments: {names}" if names else "")
                    + ".")
        if not self._views_confirm_write(
                "send_email", "Send email", sentence):
            receipt.setText("Not sent.")
            return
        receipt.setText("Sending…")
        try:
            result = google_services.send_email(
                to, subject, body, attachments=attachments or None)
        except Exception as exc:  # noqa: BLE001
            result = f"Couldn't send that mail: {exc}"
        if outcome_kind(result) == "failed":
            receipt.setText(f"Failed — {result}")
            retry = QPushButton("Retry — back to review")
            retry.clicked.connect(self._views_compose_show_review)
            try:
                receipt.parentWidget().layout().addWidget(retry)
            except Exception:  # noqa: BLE001
                pass
            return
        receipt.setText(str(result))
        self._views_toast(str(result))
        self._views_compose_draft = None
        self._views_compose_saved = None
        self._views_close_drawer(self._views_inbox_drawer,
                                 self._views_inbox_main)
        self._refresh_inbox_page()

    # ------------------------------------------------------------------
    # Contacts (index 10)
    # ------------------------------------------------------------------
    def _build_contacts_page(self) -> QWidget:
        page, content = self._page_shell(
            "Contacts",
            "People you keep — pick one to see their details.",
            kicker=True,
        )
        main = QWidget()
        main_lay = QVBoxLayout(main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(10)
        self._views_contacts_main = main
        head = QHBoxLayout()
        head.setSpacing(8)
        self._views_contacts_search = QLineEdit()
        self._views_contacts_search.setPlaceholderText(
            "Search by name or detail")
        self._views_contacts_search.textChanged.connect(
            lambda _t: self._refresh_contacts_page())
        head.addWidget(self._views_contacts_search, 1)
        self._views_contacts_add = QPushButton("Add contact")
        self._views_contacts_add.clicked.connect(
            lambda: self._views_contact_open_create())
        head.addWidget(self._views_contacts_add)
        main_lay.addLayout(head)
        self._views_contacts_count = QLabel("")
        self._views_contacts_count.setObjectName("bodySmall")
        main_lay.addWidget(self._views_contacts_count)
        self._views_contacts_empty = EmptyState(
            sentence="No contacts saved yet.",
            suggested_task="Add a contact to keep their phone, email "
            "or address in one place.",
            action_label="Add contact")
        self._views_contacts_empty.action_requested.connect(
            self._views_contacts_empty_action)
        main_lay.addWidget(self._views_contacts_empty)
        self._views_contacts_scroll = QScrollArea()
        self._views_contacts_scroll.setWidgetResizable(True)
        self._views_contacts_scroll.setFrameShape(QFrame.NoFrame)
        self._views_contacts_inner = QWidget()
        self._views_contacts_grid = QGridLayout(self._views_contacts_inner)
        self._views_contacts_grid.setContentsMargins(0, 0, 0, 0)
        self._views_contacts_grid.setSpacing(12)
        self._views_contacts_scroll.setWidget(self._views_contacts_inner)
        # QScrollArea.setWidget forces autoFillBackground True on
        # its widget — and the viewport follows — so the area
        # would paint its palette Window (white) over the dark
        # page. Reset both AFTER setWidget, so the page's dark
        # surface shows through (the firstopen_prompt pattern).
        self._views_contacts_inner.setAutoFillBackground(False)
        self._views_contacts_scroll.viewport().setAutoFillBackground(
            False)
        main_lay.addWidget(self._views_contacts_scroll, 1)
        foot = QHBoxLayout()
        self._views_contacts_refresh = QPushButton("Refresh")
        self._views_contacts_refresh.clicked.connect(
            lambda: self._refresh_contacts_page())
        foot.addWidget(self._views_contacts_refresh)
        foot.addStretch(1)
        main_lay.addLayout(foot)
        self._views_contacts_drawer = ContextDrawer(title="Contact")
        self._views_contacts_drawer.setObjectName("viewsContactsDrawer")
        self._views_contacts_drawer.hide()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(main, 1)
        row.addWidget(self._views_contacts_drawer)
        content.addLayout(row)
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "surface_1": SURFACE_1,
            "line_subtle": LINE_SUBTLE, "line_strong": LINE_STRONG,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        self._views_contacts_loaded = False
        self._views_contacts = []
        self._views_contact_name = None
        return page

    def _views_contacts_empty_action(self) -> None:
        if self._views_contacts_search.text().strip() and \
                self._views_contacts:
            self._views_contacts_search.clear()
            return
        self._views_contact_open_create()

    def _views_contact_columns(self) -> int:
        w = self.width()
        if w >= 1200:
            return 3
        if w >= 800:
            return 2
        return 1

    def _views_contact_tint(self, name: str) -> str:
        tints = ("#7C6AA8", "#5B8C7B", "#8C6B4A", "#5B7A9C", "#9C5B74")
        idx = sum(name.encode("utf-8", "ignore")) % len(tints)
        return tints[idx]

    def _refresh_contacts_page(self) -> None:
        if getattr(self, "_views_contacts_search", None) is None:
            return
        if self._views_private_on():
            self._views_contacts_loaded = False
            self._views_contacts_count.setText(
                "Private mode is on — contact names are hidden.")
            self._views_contacts_empty.hide()
            self._views_contacts_scroll.hide()
            self._views_contacts_add.hide()
            self._views_contacts_drawer.hide()
            return
        self._views_contacts_add.show()
        from voice_assistant.core.gui_facade import skills_admin_schedules
        try:
            people = skills_admin_schedules.list_contacts()
        except Exception as exc:  # noqa: BLE001 — read failed, say so
            self._views_contacts = []
            self._views_contacts_loaded = True
            self._views_contacts_count.setText(
                f"Contacts could not be read ({exc}).")
            self._views_contacts_empty.hide()
            self._views_contacts_scroll.hide()
            return
        self._views_contacts = people if isinstance(people, list) else []
        self._views_contacts_loaded = True
        query = self._views_contacts_search.text().strip().lower()
        shown = []
        for person in self._views_contacts:
            name = (person.get("person") or "").strip()
            details = [d for d in (person.get("details") or []) if d]
            if query and query not in name.lower() and not any(
                    query in d.lower() for d in details):
                continue
            shown.append((name, details))
        total = len(self._views_contacts)
        self._views_contacts_count.setText(
            f"{len(shown)} of {total} contact{'s' if total != 1 else ''}"
            + (" — filtered by search" if query else ""))
        # Hunt fix (c) item 15: hide the count at zero — the
        # EmptyState ("No contacts saved yet.") is the one empty
        # line, not "0 of 0 contacts" + the empty state.
        self._views_contacts_count.setVisible(total > 0)
        while self._views_contacts_grid.count():
            item = self._views_contacts_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if not self._views_contacts:
            self._views_contacts_empty.set_sentence(
                "No contacts saved yet.")
            if self._views_contacts_empty._button is not None:
                self._views_contacts_empty._button.setText("Add contact")
            self._views_contacts_empty.show()
            self._views_contacts_scroll.hide()
            return
        if not shown:
            self._views_contacts_empty.set_sentence(
                "No contacts match that search.")
            if self._views_contacts_empty._button is not None:
                self._views_contacts_empty._button.setText("Clear search")
            self._views_contacts_empty.show()
            self._views_contacts_scroll.hide()
            return
        self._views_contacts_empty.hide()
        self._views_contacts_scroll.show()
        cols = self._views_contact_columns()
        for idx, (name, details) in enumerate(shown):
            card = self._build_views_contact_card(name, details)
            self._views_contacts_grid.addWidget(card, idx // cols, idx % cols)
        # Every column shares the width: only the last column
        # used to stretch, so with two contacts in three columns
        # the cards sized to their content (~165 px) and the
        # values wrapped mid-token. Stretched, a card is a full
        # column wide and its value has room on one line.
        for col in range(cols):
            self._views_contacts_grid.setColumnStretch(col, 1)
        rows = (len(shown) + cols - 1) // cols
        self._views_contacts_grid.setRowStretch(rows, 1)
        # The first contact's detail pane opens with the page:
        # two small cards over a void read as unfinished, and the
        # detail pane is the page's other half. Only when nothing
        # has been picked yet and the drawer is not already open
        # (a create form, for example, is never yanked away).
        if (self._views_contact_name is None and shown
                and not self._views_contacts_drawer.isVisible()):
            self._on_views_contact_picked(shown[0][0])

    def _build_views_contact_card(self, name: str, details: list) -> QWidget:
        card = _ViewsCard()
        # Fix round (2026-11-25): a card is born per refresh, after
        # the appearance pass — theme it from the window's stored
        # resolved theme, or it keeps the dark house face and is
        # unreadable on Light (the StatusPill shape).
        _resolved = getattr(self, "_resolved_theme", None)
        if _resolved:
            card.set_theme(_resolved)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        top = QHBoxLayout()
        disc = QLabel(_views_initials(name))
        disc.setFixedSize(48, 48)
        disc.setAlignment(Qt.AlignCenter)
        disc.setStyleSheet(
            "border-radius: 24px; background: %s; color: #FFFFFF; "
            "font-size: 17px; font-weight: 600;"
            % self._views_contact_tint(name))
        top.addWidget(disc)
        name_lbl = QLabel(name)
        name_lbl.setObjectName("bodyStrong")
        name_lbl.setWordWrap(True)
        top.addWidget(name_lbl, 1)
        layout.addLayout(top)
        # A detail is a key row over its value row, never
        # "key: value" on one line: the card used to be too
        # narrow for label-plus-value, so the value wrapped
        # mid-token ("phone: +44 7000" / "000001"). Stacked, the
        # value has the full card width; and it never wraps at
        # all — a phone number or an email address is one token,
        # so a too-long value is clipped by the card edge
        # rather than split across lines.
        for detail in details[:4]:
            key, _sep, value = str(detail).partition(": ")
            if _sep:
                key_lbl = QLabel(key)
                key_lbl.setObjectName("muted")
                layout.addWidget(key_lbl)
                row = QLabel(value)
            else:
                row = QLabel(str(detail))
            row.setObjectName("bodySmall")
            row.setWordWrap(False)
            layout.addWidget(row)
        if len(details) > 4:
            more = QLabel(f"+{len(details) - 4} more")
            more.setObjectName("bodySmall")
            layout.addWidget(more)
        # A bottom-anchored footer, so a contact with one detail
        # still reads as a complete card: the card stretches to
        # its row height (its neighbour may carry four details),
        # and without the footer the short card is one line in a
        # big box. The footer pins to the card's bottom edge.
        layout.addStretch(1)
        foot = QLabel(
            f"{len(details)} detail"
            f"{'s' if len(details) != 1 else ''} — click to open")
        foot.setObjectName("muted")
        layout.addWidget(foot)
        card.chosen.connect(
            lambda _name=name: self._on_views_contact_picked(_name))
        return card

    def _on_views_contact_picked(self, name: str) -> None:
        if self._views_private_on():
            return
        self._views_contact_name = name
        self._views_contact_open_drawer(name)

    def _views_contact_open_create(self) -> None:
        if self._views_private_on():
            return
        self._views_contact_name = None
        self._views_contact_open_drawer(None)

    def _views_contact_open_drawer(self, name) -> None:
        self._views_open_drawer(self._views_contacts_drawer,
                                self._views_contacts_main)
        self._views_contact_drawer_form(name)

    def _views_contact_drawer_form(self, name) -> None:
        drawer = self._views_contacts_drawer
        layout = drawer.content_layout()
        self._views_drawer_clear(drawer)
        creating = name is None
        if self._views_private_on():
            note = QLabel("Private mode is on — contact details are hidden.")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addLayout(self._views_drawer_close_row(
                drawer, self._views_contacts_main))
            layout.addStretch(1)
            return
        details = []
        if not creating:
            for person in (self._views_contacts or []):
                if person.get("person") == name:
                    details = list(person.get("details") or [])
                    break
        if creating:
            name_edit = QLineEdit()
            name_edit.setPlaceholderText("Name")
            name_edit.setObjectName("viewsContactName")
            layout.addWidget(name_edit)
            detail_edit = QLineEdit()
            detail_edit.setPlaceholderText(
                "First detail — phone, email or address")
            detail_edit.setObjectName("viewsContactDetail")
            layout.addWidget(detail_edit)
            hint = QLabel(
                "Names are kept to 80 characters, details to 200.")
            hint.setObjectName("bodySmall")
            hint.setWordWrap(True)
            layout.addWidget(hint)
            save = QPushButton("Save contact")
            save.setObjectName("viewsContactSave")
            save.clicked.connect(
                lambda: self._views_contact_save_new(
                    name_edit.text(), detail_edit.text()))
            layout.addWidget(save)
            layout.addLayout(self._views_drawer_close_row(
                drawer, self._views_contacts_main))
            layout.addStretch(1)
            return
        heading = QLabel(name)
        heading.setObjectName("bodyStrong")
        layout.addWidget(heading)
        src = QLabel("Saved by you — in your contacts.")
        src.setObjectName("bodySmall")
        layout.addWidget(src)
        if not details:
            note = QLabel("No details saved for this contact yet.")
            note.setWordWrap(True)
            layout.addWidget(note)
        for detail in details:
            # A detail is a key row over its value row, never
            # "key: value" on one line — the same stacking as the
            # cards. The pane used to show one wrapping label in
            # a narrow column, so the value split mid-token
            # ("phone: +44 7000" / "000001"). Stacked, the value
            # is one unbroken label that never wraps at all.
            # Step 2 F2: the value used to share its row with
            # the three buttons, so at 1280 the drawer (380 px)
            # left ~126 px for the value — about 21 characters
            # before it clipped with no way to see the rest.
            # The buttons sit on their own row below the value
            # now, so the value has the full drawer width; Copy
            # / Edit / Delete stay one tap away.
            text_col = QVBoxLayout()
            text_col.setContentsMargins(0, 0, 0, 0)
            text_col.setSpacing(0)
            key, _sep, value = str(detail).partition(": ")
            if _sep:
                key_lbl = QLabel(key)
                key_lbl.setObjectName("muted")
                text_col.addWidget(key_lbl)
                val_lbl = QLabel(value)
            else:
                val_lbl = QLabel(str(detail))
            val_lbl.setObjectName("bodySmall")
            val_lbl.setWordWrap(False)
            text_col.addWidget(val_lbl)
            layout.addLayout(text_col)
            btn_row = QHBoxLayout()
            btn_row.setContentsMargins(0, 0, 0, 0)
            btn_row.setSpacing(6)
            copy_btn = QPushButton("Copy")
            copy_btn.clicked.connect(
                lambda _c, _d=detail: self._views_contact_copy(_d))
            btn_row.addWidget(copy_btn)
            edit_btn = QPushButton("Edit")
            edit_btn.clicked.connect(
                lambda _c, _d=detail: self._views_contact_edit_detail(
                    name, _d))
            btn_row.addWidget(edit_btn)
            del_btn = QPushButton("Delete")
            del_btn.clicked.connect(
                lambda _c, _d=detail: self._views_contact_delete_detail(
                    name, _d))
            btn_row.addWidget(del_btn)
            btn_row.addStretch(1)
            layout.addLayout(btn_row)
        add_row = QHBoxLayout()
        add_edit = QLineEdit()
        add_edit.setPlaceholderText("Add a detail — phone, email or address")
        add_edit.setObjectName("viewsContactAddDetail")
        add_row.addWidget(add_edit, 1)
        add_btn = QPushButton("Add")
        add_btn.setObjectName("viewsContactAdd")
        add_btn.clicked.connect(
            lambda: self._views_contact_add_detail(
                name, add_edit.text()))
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)
        rename_row = QHBoxLayout()
        rename_edit = QLineEdit()
        rename_edit.setPlaceholderText("Rename — new name for this contact")
        rename_edit.setObjectName("viewsContactRename")
        rename_row.addWidget(rename_edit, 1)
        rename_btn = QPushButton("Rename")
        rename_btn.setObjectName("viewsContactRenameSave")
        rename_btn.clicked.connect(
            lambda: self._views_contact_rename(
                name, rename_edit.text()))
        rename_row.addWidget(rename_btn)
        layout.addLayout(rename_row)
        self._views_contact_related(name, details, layout)
        layout.addLayout(self._views_drawer_close_row(
            drawer, self._views_contacts_main))
        layout.addStretch(1)

    def _views_contact_copy(self, detail: str) -> None:
        QApplication.clipboard().setText(detail)
        self._views_toast("Copied to clipboard.")

    def _views_contact_save_new(self, name: str, detail: str) -> None:
        from voice_assistant.core.gui_facade import skills_admin_schedules
        from voice_assistant.core.gui_facade import outcome_kind
        name = (name or "").strip()
        detail = (detail or "").strip()
        if not name:
            self._views_toast("A name is needed before saving.")
            return
        if len(name) > _MAX_CONTACT_NAME_CHARS:
            self._views_toast(
                f"Names are kept to {_MAX_CONTACT_NAME_CHARS} characters — "
                "shorten it and try again.")
            return
        if not detail:
            self._views_toast("Add at least one detail before saving.")
            return
        if len(detail) > _MAX_CONTACT_DETAIL_CHARS:
            self._views_toast(
                f"Details are kept to {_MAX_CONTACT_DETAIL_CHARS} characters "
                "— shorten it and try again.")
            return
        result = skills_admin_schedules.contact_card(action="save", person=name, detail=detail)
        if outcome_kind(result) == "failed":
            self._views_toast(
                f"Could not save that contact ({str(result)}).")
            return
        self._views_toast(str(result))
        self._views_close_drawer(self._views_contacts_drawer,
                                 self._views_contacts_main)
        # Land on the contact just created, not on the first
        # contact: the refresh auto-opens the first contact only
        # when nothing has been picked, and a create is a pick.
        self._views_contact_name = name
        self._refresh_contacts_page()
        self._on_views_contact_picked(name)

    def _views_contact_add_detail(self, name: str, detail: str) -> None:
        from voice_assistant.core.gui_facade import skills_admin_schedules
        from voice_assistant.core.gui_facade import outcome_kind
        detail = (detail or "").strip()
        if not detail:
            self._views_toast("Write the detail before adding it.")
            return
        if len(detail) > _MAX_CONTACT_DETAIL_CHARS:
            self._views_toast(
                f"Details are kept to {_MAX_CONTACT_DETAIL_CHARS} characters "
                "— shorten it and try again.")
            return
        result = skills_admin_schedules.contact_card(action="save", person=name, detail=detail)
        if outcome_kind(result) == "failed":
            self._views_toast(
                f"Could not add that detail ({str(result)}).")
            return
        self._views_toast(str(result))
        self._refresh_contacts_page()
        self._views_contact_drawer_form(name)

    def _views_contact_edit_detail(self, name: str, old_detail: str) -> None:
        drawer = self._views_contacts_drawer
        layout = drawer.content_layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            sub = item.layout()
            while sub is not None and sub.count():
                inner = sub.takeAt(0)
                widget = inner.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
        heading = QLabel(f"Edit detail — {name}")
        heading.setObjectName("bodyStrong")
        layout.addWidget(heading)
        note = QLabel(f"Current: {old_detail}")
        # One unbroken line: the note used to wrap mid-token
        # like the detail rows did. The edit field below holds
        # the full detail, so a too-long note clips rather
        # than splits.
        note.setWordWrap(False)
        layout.addWidget(note)
        edit = QLineEdit(old_detail)
        edit.setObjectName("viewsContactEditDetail")
        layout.addWidget(edit)
        hint = QLabel(
            f"Details are kept to {_MAX_CONTACT_DETAIL_CHARS} characters.")
        hint.setObjectName("bodySmall")
        layout.addWidget(hint)
        row = QHBoxLayout()
        save = QPushButton("Save")
        save.setObjectName("viewsContactEditSave")
        save.clicked.connect(
            lambda: self._views_contact_save_edited(
                name, old_detail, edit.text()))
        row.addWidget(save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(
            lambda: self._views_contact_drawer_form(name))
        row.addWidget(cancel)
        layout.addLayout(row)
        layout.addStretch(1)

    def _views_contact_save_edited(self, name: str, old_detail: str,
                                   new_detail: str) -> None:
        from voice_assistant.core.gui_facade import skills_admin_schedules
        from voice_assistant.core.gui_facade import skills_admin_schedules
        from voice_assistant.core.gui_facade import outcome_kind
        new_detail = (new_detail or "").strip()
        if not new_detail:
            self._views_toast("The detail cannot be empty.")
            return
        if len(new_detail) > _MAX_CONTACT_DETAIL_CHARS:
            self._views_toast(
                f"Details are kept to {_MAX_CONTACT_DETAIL_CHARS} characters "
                "— shorten it and try again.")
            return
        if new_detail == old_detail:
            self._views_contact_drawer_form(name)
            return
        saved = skills_admin_schedules.contact_card(action="save", person=name, detail=new_detail)
        if outcome_kind(saved) == "failed":
            self._views_toast(
                "Could not save the new detail "
                f"({str(saved)}) — the old one is untouched.")
            return
        removed = skills_admin_schedules.delete_contact_detail(name, old_detail)
        if outcome_kind(removed) == "failed":
            self._views_toast(
                "The new detail is saved, but the old one could not be "
                "removed — try deleting it again.")
            self._refresh_contacts_page()
            self._views_contact_drawer_form(name)
            return
        self._views_toast("Detail updated.")
        self._refresh_contacts_page()
        self._views_contact_drawer_form(name)

    def _views_contact_delete_detail(self, name: str, detail: str) -> None:
        from voice_assistant.core.gui_facade import skills_admin_schedules
        from voice_assistant.core.gui_facade import outcome_kind
        if not self._views_confirm_write(
                "delete_contact_detail", "Delete contact detail",
                f"Delete this detail for {name}?\n\n{detail}"):
            return
        result = skills_admin_schedules.delete_contact_detail(name, detail)
        if outcome_kind(result) == "failed":
            self._views_toast(
                f"Could not delete that detail ({str(result)}).")
            return
        self._views_toast(str(result))
        self._refresh_contacts_page()
        remaining = []
        for person in (self._views_contacts or []):
            if person.get("person") == name:
                remaining = list(person.get("details") or [])
                break
        if remaining:
            self._views_contact_drawer_form(name)
        else:
            self._views_close_drawer(self._views_contacts_drawer,
                                     self._views_contacts_main)

    def _views_contact_rename(self, name: str, new_name: str) -> None:
        from voice_assistant.core.gui_facade import skills_admin_schedules
        from voice_assistant.core.gui_facade import skills_admin_schedules
        from voice_assistant.core.gui_facade import outcome_kind
        new_name = (new_name or "").strip()
        if not new_name:
            self._views_toast("Write the new name first.")
            return
        if len(new_name) > _MAX_CONTACT_NAME_CHARS:
            self._views_toast(
                f"Names are kept to {_MAX_CONTACT_NAME_CHARS} characters — "
                "shorten it and try again.")
            return
        if new_name == name:
            return
        if not self._views_confirm_write(
                "rename_contact", "Rename contact",
                f"Rename {name} to {new_name}? Every detail moves with "
                "the new name."):
            return
        details = []
        for person in (self._views_contacts or []):
            if person.get("person") == name:
                details = list(person.get("details") or [])
                break
        saved = 0
        for detail in details:
            result = skills_admin_schedules.contact_card(
                action="save", person=new_name, detail=detail)
            if outcome_kind(result) != "failed":
                saved += 1
        if saved != len(details):
            self._views_toast(
                f"Only {saved} of {len(details)} details moved to "
                f"{new_name} — the old ones are untouched.")
            return
        failed = 0
        for detail in details:
            removed = skills_admin_schedules.delete_contact_detail(name, detail)
            if outcome_kind(removed) == "failed":
                failed += 1
        if failed:
            self._views_toast(
                f"Renamed to {new_name}, but {failed} old detail"
                f"{'s' if failed != 1 else ''} could not be removed "
                "— delete them from the old contact.")
            self._refresh_contacts_page()
            self._views_contact_drawer_form(new_name)
            return
        self._views_toast(f"Renamed to {new_name}.")
        self._views_close_drawer(self._views_contacts_drawer,
                                 self._views_contacts_main)
        self._refresh_contacts_page()

    def _views_contact_related(self, name: str, details: list,
                               layout) -> None:
        # §4.4 — exact match against the stored details, on the VALUE
        # (the part after "key: "), never on the raw "key: value" string
        # (a bare number never equals "phone: +44 …") and never a
        # substring (P3-10). The section appears only when a link
        # exists — never an empty "Related" heading.
        def _value(detail):
            if ": " in detail:
                return detail.split(": ", 1)[1].strip()
            return detail.strip()

        phone_values = [_value(d) for d in details
                        if any(c.isdigit() for c in d)]
        email_values = [_value(d).lower() for d in details if "@" in d]
        if not phone_values and not email_values:
            return
        matched_calls = []
        if phone_values:
            try:
                from voice_assistant.core.gui_facade import voice_agent
                store = voice_agent.VoiceAgentStore()
                rows = store.list_calls(limit=50)
            except Exception:  # noqa: BLE001 — no calls then
                rows = []
            for row in rows or []:
                number = str(row.get("number") or "")
                if number and number in phone_values:
                    matched_calls.append(row)
        matched_msgs = []
        if email_values:
            try:
                from voice_assistant.core.gui_facade import google_services
                msgs = google_services.recent_messages(20) or []
            except Exception:  # noqa: BLE001 — no mail then
                msgs = []
            for msg in msgs:
                sender = _views_sender_address(msg.get("sender") or "")
                if sender and sender.lower() in email_values:
                    matched_msgs.append(msg)
        if not matched_calls and not matched_msgs:
            return
        # §4.4 — the link is shown as inferred, never asserted.
        kinds = []
        if matched_calls:
            kinds.append("matching number")
        if matched_msgs:
            kinds.append("matching email")
        heading = QLabel("Related — Linked by " + " and ".join(kinds))
        heading.setObjectName("bodyStrong")
        # Fix round (2026-11-25): the heading is wider than the
        # drawer rail — it wrapped to nothing and read "…matching
        # emai". It wraps inside the rail now; the full wording
        # stays readable. (Never widen the drawer or shrink the
        # font for it.)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        for row in matched_calls:
            # Fix round (2026-11-25): the button printed the raw
            # epoch float ("Call — 1795622484.1419365
            # (completed)") — the bug-10 helper formats it, but
            # the button bypassed it. Same shape as the card meta.
            when = _views_call_when(row.get("started"),
                                    row.get("ended"))
            label = (f"Call — {when} ({row.get('outcome') or ''})"
                     if when
                     else f"Call ({row.get('outcome') or ''})")
            btn = QPushButton(label)
            btn.clicked.connect(
                lambda _c, _id=row.get("id"): self._views_call_open(_id))
            layout.addWidget(btn)
        for msg in matched_msgs:
            btn = QPushButton(
                f"Email — {msg.get('subject') or '(no subject)'}")
            btn.clicked.connect(
                lambda _c, _m=msg: self._views_inbox_open_related(_m))
            layout.addWidget(btn)

    def _views_inbox_open_related(self, message: dict) -> None:
        self._views_close_drawer(self._views_contacts_drawer,
                                 self._views_contacts_main)
        self._show_page(9)
        self._views_inbox_show_message(str(message.get("id") or ""))

    # ------------------------------------------------------------------
    # Calls (index 11)
    # ------------------------------------------------------------------
    def _build_calls_page(self) -> QWidget:
        page, content = self._page_shell(
            "Calls",
            "Calls made or taken through the voice agent.",
            kicker=True,
        )
        main = QWidget()
        main_lay = QVBoxLayout(main)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(10)
        self._views_calls_main = main
        head = QHBoxLayout()
        head.setSpacing(8)
        self._views_calls_search = QLineEdit()
        self._views_calls_search.setPlaceholderText(
            "Search by number or agent")
        self._views_calls_search.textChanged.connect(
            lambda _t: self._refresh_calls_page())
        head.addWidget(self._views_calls_search, 1)
        self._views_calls_direction = QComboBox()
        self._views_calls_direction.addItems(
            ["All directions", "Outbound", "Inbound"])
        self._views_calls_direction.currentIndexChanged.connect(
            lambda _i: self._refresh_calls_page())
        head.addWidget(self._views_calls_direction)
        self._views_calls_outcome = QComboBox()
        self._views_calls_outcome.addItems(["All outcomes"])
        self._views_calls_outcome.currentIndexChanged.connect(
            lambda _i: self._refresh_calls_page())
        head.addWidget(self._views_calls_outcome)
        self._views_calls_date = QDateEdit()
        self._views_calls_date.setCalendarPopup(True)
        self._views_calls_date.setDisplayFormat("yyyy-MM-dd")
        self._views_calls_date.setDate(QDate.currentDate())
        self._views_calls_date.dateChanged.connect(
            lambda _d: self._views_calls_set_date(
                self._views_calls_date.date()))
        head.addWidget(self._views_calls_date)
        self._views_calls_clear_date = QPushButton("Any date")
        self._views_calls_clear_date.clicked.connect(
            lambda: self._views_calls_set_date(None))
        head.addWidget(self._views_calls_clear_date)
        main_lay.addLayout(head)
        self._views_calls_status = QLabel("")
        self._views_calls_status.setObjectName("bodySmall")
        main_lay.addWidget(self._views_calls_status)
        self._views_calls_empty = EmptyState(
            sentence="No calls yet.",
            suggested_task="Calls made or taken through the voice agent "
            "will appear here.")
        main_lay.addWidget(self._views_calls_empty)
        self._views_calls_scroll = QScrollArea()
        self._views_calls_scroll.setWidgetResizable(True)
        self._views_calls_scroll.setFrameShape(QFrame.NoFrame)
        self._views_calls_inner = QWidget()
        self._views_calls_timeline = QVBoxLayout(self._views_calls_inner)
        self._views_calls_timeline.setContentsMargins(0, 0, 0, 0)
        self._views_calls_timeline.setSpacing(10)
        self._views_calls_scroll.setWidget(self._views_calls_inner)
        # QScrollArea.setWidget forces autoFillBackground True —
        # see the Contacts scroll area above for the same fix.
        self._views_calls_inner.setAutoFillBackground(False)
        self._views_calls_scroll.viewport().setAutoFillBackground(False)
        main_lay.addWidget(self._views_calls_scroll, 1)
        self._views_calls_more = QPushButton("Load more")
        self._views_calls_more.clicked.connect(
            lambda: self._views_calls_load_more())
        main_lay.addWidget(self._views_calls_more)
        foot = QHBoxLayout()
        self._views_calls_refresh = QPushButton("Refresh")
        self._views_calls_refresh.clicked.connect(
            lambda: self._refresh_calls_page())
        foot.addWidget(self._views_calls_refresh)
        foot.addStretch(1)
        main_lay.addLayout(foot)
        self._views_calls_drawer = ContextDrawer(title="Call")
        self._views_calls_drawer.setObjectName("viewsCallsDrawer")
        self._views_calls_drawer.hide()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(main, 1)
        row.addWidget(self._views_calls_drawer)
        content.addLayout(row)
        self._refresh_views_theme({
            "surface_2": SURFACE_2, "surface_1": SURFACE_1,
            "line_subtle": LINE_SUBTLE, "line_strong": LINE_STRONG,
            "text_muted": TEXT_MUTED, "text": TEXT, "accent": ACCENT})
        self._views_calls_limit = _CALLS_PAGE_SIZE
        self._views_calls_loaded = False
        self._views_calls = []
        self._views_calls_date_filter = None
        self._views_call_id = None
        return page

    def _views_calls_set_date(self, date) -> None:
        self._views_calls_date_filter = date
        if date is not None:
            self._views_calls_date.setDate(date)
        self._refresh_calls_page()

    def _views_calls_load_more(self) -> None:
        self._views_calls_limit += _CALLS_PAGE_SIZE
        self._refresh_calls_page()

    def _refresh_calls_page(self) -> None:
        if getattr(self, "_views_calls_search", None) is None:
            return
        if self._views_private_on():
            self._views_calls_loaded = False
            self._views_calls_status.setText(
                "Private mode is on — call numbers are hidden.")
            self._views_calls_empty.hide()
            self._views_calls_scroll.hide()
            self._views_calls_more.hide()
            self._views_calls_drawer.hide()
            return
        try:
            from voice_assistant.core.gui_facade import voice_agent
            store = voice_agent.VoiceAgentStore()
            rows = store.list_calls(limit=self._views_calls_limit)
        except Exception as exc:  # noqa: BLE001 — read failed, say so
            self._views_calls = []
            self._views_calls_loaded = True
            self._views_calls_status.setText(
                f"Calls could not be read ({exc}).")
            self._views_calls_empty.hide()
            self._views_calls_scroll.hide()
            self._views_calls_more.hide()
            return
        self._views_calls = rows if isinstance(rows, list) else []
        self._views_calls_loaded = True
        outcomes = sorted({str(r.get("outcome") or "") for r in rows
                           if r.get("outcome")})
        current = self._views_calls_outcome.currentText()
        self._views_calls_outcome.blockSignals(True)
        self._views_calls_outcome.clear()
        self._views_calls_outcome.addItems(["All outcomes"] + outcomes)
        if current in ["All outcomes"] + outcomes:
            self._views_calls_outcome.setCurrentText(current)
        self._views_calls_outcome.blockSignals(False)
        query = self._views_calls_search.text().strip().lower()
        direction = self._views_calls_direction.currentText()
        outcome = self._views_calls_outcome.currentText()
        date_filter = self._views_calls_date_filter
        shown = []
        for row in self._views_calls:
            if query and query not in str(row.get("number") or "").lower() \
                    and query not in str(row.get("agent_id") or "").lower():
                continue
            if direction == "Outbound" and row.get("direction") != "out":
                continue
            if direction == "Inbound" and row.get("direction") != "in":
                continue
            if outcome != "All outcomes" and \
                    str(row.get("outcome")) != outcome:
                continue
            if date_filter is not None:
                started = str(row.get("started") or "")
                if not started.startswith(
                        date_filter.toString("yyyy-MM-dd")):
                    continue
            shown.append(row)
        total = len(self._views_calls)
        self._views_calls_status.setText(
            f"Showing the latest {total} call{'s' if total != 1 else ''}"
            + (f" — {len(shown)} match the filters"
               if len(shown) != total else ""))
        # Hunt fix (c) item 15: hide the status at zero — the
        # EmptyState ("No calls yet.") is the one empty line, not
        # "Showing the latest 0 calls" + the empty state.
        self._views_calls_status.setVisible(total > 0)
        while self._views_calls_timeline.count():
            item = self._views_calls_timeline.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if not self._views_calls:
            self._views_calls_empty.set_sentence("No calls yet.")
            self._views_calls_empty.show()
            self._views_calls_scroll.hide()
            self._views_calls_more.hide()
            return
        if not shown:
            self._views_calls_empty.set_sentence(
                "No calls match those filters.")
            self._views_calls_empty.show()
            self._views_calls_scroll.hide()
            self._views_calls_more.hide()
            return
        self._views_calls_empty.hide()
        self._views_calls_scroll.show()
        self._views_calls_more.setVisible(
            len(self._views_calls) >= self._views_calls_limit)
        for row in shown:
            self._views_calls_timeline.addWidget(
                self._build_views_call_card(row))
        self._views_calls_timeline.addStretch(1)

    def _build_views_call_card(self, row: dict) -> QWidget:
        card = _ViewsCard()
        # Fix round (2026-11-25): a card is born per refresh, after
        # the appearance pass — theme it from the window's stored
        # resolved theme, or it keeps the dark house face and is
        # unreadable on Light (the StatusPill shape).
        _resolved = getattr(self, "_resolved_theme", None)
        if _resolved:
            card.set_theme(_resolved)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        top = QHBoxLayout()
        number = str(row.get("number") or "")
        agent = str(row.get("agent") or row.get("agent_id") or "")
        title = number
        if agent:
            title = f"{number} — {agent}" if number else agent
        title_lbl = QLabel(title or "(no number)")
        title_lbl.setObjectName("bodyStrong")
        title_lbl.setWordWrap(True)
        top.addWidget(title_lbl, 1)
        pill = StatusPill(
            text=str(row.get("outcome") or ""),
            severity=_outcome_severity(str(row.get("outcome") or "")))
        # Phase 0: a pill is born per refresh, after the
        # appearance pass — theme it from the window's stored
        # resolved theme, or its words paint the house
        # (near-white) text and vanish on Light.
        _resolved = getattr(self, "_resolved_theme", None)
        if _resolved:
            pill.set_theme(_resolved)
        top.addWidget(pill)
        layout.addLayout(top)
        when = _views_call_when(row.get("started"), row.get("ended"))
        direction = "Outbound" if row.get("direction") == "out" else "Inbound"
        meta = QLabel(f"{direction} · {when}" if when else direction)
        meta.setObjectName("bodySmall")
        meta.setWordWrap(True)
        layout.addWidget(meta)
        call_id = row.get("id")
        card.chosen.connect(
            lambda _id=call_id: self._on_views_call_picked(_id))
        return card

    def _on_views_call_picked(self, call_id) -> None:
        if self._views_private_on():
            return
        self._views_call_id = call_id
        self._views_call_open(call_id)

    def _views_call_open(self, call_id) -> None:
        if self._views_private_on():
            return
        self._views_open_drawer(self._views_calls_drawer,
                                self._views_calls_main)
        self._views_call_drawer(call_id)

    def _views_call_drawer(self, call_id) -> None:
        drawer = self._views_calls_drawer
        layout = drawer.content_layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            sub = item.layout()
            while sub is not None and sub.count():
                inner = sub.takeAt(0)
                widget = inner.widget()
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
        if self._views_private_on():
            note = QLabel("Private mode is on — call numbers are hidden.")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addLayout(self._views_drawer_close_row(
                drawer, self._views_calls_main))
            layout.addStretch(1)
            return
        try:
            from voice_assistant.core.gui_facade import voice_agent
            store = voice_agent.VoiceAgentStore()
            row = store.get_call(call_id)
        except Exception:  # noqa: BLE001 — read failed, say so
            row = None
        if not row:
            note = QLabel("That call could not be found.")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addLayout(self._views_drawer_close_row(
                drawer, self._views_calls_main))
            layout.addStretch(1)
            return
        heading = QLabel(str(row.get("number") or "Call"))
        heading.setObjectName("bodyStrong")
        layout.addWidget(heading)
        direction = "Outbound" if row.get("direction") == "out" else "Inbound"
        when = _views_call_when(row.get("started"), row.get("ended"))
        meta = QLabel(
            f"{direction} · {when}" if when else direction)
        meta.setObjectName("bodySmall")
        meta.setWordWrap(True)
        layout.addWidget(meta)
        pill = StatusPill(
            text=str(row.get("outcome") or ""),
            severity=_outcome_severity(str(row.get("outcome") or "")))
        # Phase 0: a pill is born per refresh, after the
        # appearance pass — theme it from the window's stored
        # resolved theme, or its words paint the house
        # (near-white) text and vanish on Light.
        _resolved = getattr(self, "_resolved_theme", None)
        if _resolved:
            pill.set_theme(_resolved)
        layout.addWidget(pill)
        if row.get("agent") or row.get("agent_id"):
            agent = QLabel(
                f"Agent: {row.get('agent') or row.get('agent_id')}")
            agent.setObjectName("bodySmall")
            layout.addWidget(agent)
        heading = QLabel("Agent flow transcript")
        heading.setObjectName("bodyStrong")
        layout.addWidget(heading)
        steps = row.get("transcript") or []
        if not steps:
            note = QLabel("No transcript recorded for this call.")
            note.setWordWrap(True)
            layout.addWidget(note)
        for idx, step in enumerate(steps, 1):
            kind = step.get("kind") if isinstance(step, dict) else ""
            detail = step.get("detail") if isinstance(step, dict) else ""
            line = QLabel(f"{idx}. {kind}: {detail}")
            line.setWordWrap(True)
            layout.addWidget(line)
        self._views_call_follow_up(row, layout)
        layout.addLayout(self._views_drawer_close_row(
            drawer, self._views_calls_main))
        layout.addStretch(1)

    def _views_call_follow_up(self, row: dict, layout) -> None:
        heading = QLabel("Follow up")
        heading.setObjectName("bodyStrong")
        layout.addWidget(heading)
        label_edit = QLineEdit(
            f"Follow up — {row.get('number') or 'call'}")
        label_edit.setObjectName("viewsCallFollowUpLabel")
        layout.addWidget(label_edit)
        when = QHBoxLayout()
        date_edit = QDateEdit(QDate.currentDate())
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("yyyy-MM-dd")
        date_edit.setObjectName("viewsCallFollowUpDate")
        when.addWidget(date_edit)
        time_edit = QTimeEdit(QTime.currentTime().addSecs(3600))
        time_edit.setDisplayFormat("HH:mm")
        time_edit.setObjectName("viewsCallFollowUpTime")
        when.addWidget(time_edit)
        layout.addLayout(when)
        msg_edit = QLineEdit()
        msg_edit.setPlaceholderText("What should the reminder say?")
        msg_edit.setObjectName("viewsCallFollowUpMessage")
        layout.addWidget(msg_edit)
        set_btn = QPushButton("Set reminder")
        set_btn.setObjectName("viewsCallFollowUpSet")
        set_btn.clicked.connect(
            lambda: self._views_call_set_reminder(
                row, label_edit.text(), date_edit.date(),
                time_edit.time(), msg_edit.text()))
        layout.addWidget(set_btn)

    def _views_call_set_reminder(self, row: dict, label: str, date,
                                 time, message: str) -> None:
        from voice_assistant.core.gui_facade import scheduler
        label = (label or "").strip() or \
            f"Follow up — {row.get('number') or 'call'}"
        message = (message or "").strip()
        fire_at = datetime.datetime.combine(
            date.toPython(), time.toPython())
        if fire_at <= datetime.datetime.now():
            fire_at = fire_at + datetime.timedelta(days=1)
        if not self._views_confirm_write(
                "add_reminder", "Set follow-up reminder",
                f"Set a reminder for {label} at "
                f"{fire_at.strftime('%Y-%m-%d %H:%M')}?"):
            return
        try:
            scheduler.add_reminder(
                label=label, fire_at=fire_at, message=message,
                repeat=False)
        except Exception as exc:  # noqa: BLE001 — write failed, say so
            self._views_toast(f"Could not set that reminder ({exc}).")
            return
        self._views_toast(
            f"Reminder set for {fire_at.strftime('%Y-%m-%d %H:%M')}.")
