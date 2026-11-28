"""MainWindow pages mixin (part): Notes / Finance / Devices (UI Phase 5).

Three first-class workspaces over the verified backends only — the
notes tool's store, the local money stores, and the Home Assistant
client. No new funnel tools, no new themes, no new QSettings keys,
no new badge. Every page is a standalone class (the Phase 4
Pulse/Senses/Doctor shape): the window builds it with no providers
and it reads the real backends; tests construct it with a providers
dict of fakes. All reads go through the same loaders the funnel
tools use — never a second implementation.

Shared rules (brief §6): _page_shell headers with the honest scope
in the subtitle; ContextDrawer for detail/edit; HudTabs for the
Finance sections; EmptyState for every empty state; StatusPill for
device states and envelope states. No new colour, no new type size,
no new spacing token, no inline style-sheet or font-size literals
in this file (the audit2s1 style pins count this file). Private mode blanks
content, keeps counts, hides write buttons, and never opens a drawer
with user content. Refresh is a re-read, never a rebuild: view,
selection, search and scroll survive it.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import home_assistant_config as _hac
from voice_assistant.core.gui_facade import homeassistant as _ha
from voice_assistant.core.gui_facade import money_alerts as _alerts
from voice_assistant.core.gui_facade import permission_grants as _grants
from voice_assistant.core.gui_facade import private_mode as _private_mode
from voice_assistant.core.gui_facade import (
    skills_money_budget as _money)
from voice_assistant.core.gui_facade import skills_money_helpers as _helpers
from voice_assistant.core.gui_facade import skills_notes as _notes

# The money formatter is a pure function on the helpers module.
_fmt = _helpers._money

# The Finance calculators live in the Skills centre; the page links
# to them, never re-implements them (brief §3). (label, tool name):
# each launcher opens the command palette with the tool name typed.
_CALCULATORS = [
    ("Work out how long a savings goal takes", "savings_goal"),
    ("Work out how an amount grows with interest", "compound_growth"),
    ("Work out a loan payment", "loan_payment"),
    ("Split a bill with tip and tax", "tip_and_tax"),
    ("Convert an hourly rate", "hourly_rate_convert"),
    ("Compare prices per unit", "price_per_unit"),
    ("Stack discounts on a price", "discount_stack"),
    ("Split rent fairly", "rent_split"),
    ("Work out a fund's returns", "fund_returns"),
]

_RUPEE = "\u20b9"


def _is_private() -> bool:
    """Private mode, guarded — a broken read reads as off."""
    try:
        return bool(_private_mode.private_mode_on())
    except Exception:  # noqa: BLE001 — private mode never breaks a page
        return False


def _call_win(win, name, *args, default=None):
    """Call a window method when it exists; `default` otherwise.
    Pages run against the real MainWindow or a test stub — a missing
    method degrades, never raises."""
    fn = getattr(win, name, None)
    if not callable(fn):
        return default
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 — a window seam never breaks a page
        return default


def _open_drawer(win, drawer, main):
    """Show a drawer through the window's shared drill-in (Phase 3
    _views_open_drawer: 640 hides the list side), or directly."""
    fn = getattr(win, "_views_open_drawer", None)
    if callable(fn):
        try:
            fn(drawer, main)
            return
        except Exception:  # noqa: BLE001
            pass
    try:
        drawer.setFixedWidth(380)
    except Exception:  # noqa: BLE001
        pass
    drawer.show()


def _close_drawer(win, drawer, main):
    fn = getattr(win, "_views_close_drawer", None)
    if callable(fn):
        try:
            fn(drawer, main)
            return
        except Exception:  # noqa: BLE001
            pass
    drawer.hide()
    if main is not None:
        try:
            main.show()
        except Exception:  # noqa: BLE001
            pass


def _card_row(parent_layout):
    """One catalogue-density row card (the shared row shape)."""
    card = QFrame()
    card.setObjectName("card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(12, 8, 12, 8)
    lay.setSpacing(2)
    parent_layout.addWidget(card)
    return card, lay


def _row_title(text):
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _row_sub(text):
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label


def _chip(text):
    btn = QPushButton(text)
    btn.setObjectName("chipBtn")
    return btn


def _clear_layout(layout):
    """Empty a layout now (the _mem_clear_layout pattern — an
    orphaned widget would keep painting at its old geometry over
    its replacement). setParent(None) FIRST, not just deleteLater:
    until the deferred delete lands, an orphaned widget keeps
    answering findChildren (the orphan trap from legs 51/54).
    Deleting the widget drops its children (and any nested
    layouts) with it; a nested layout item is cleared the same
    way, recursively."""
    if layout is None:
        return
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        sub = item.layout()
        if sub is not None:
            _clear_layout(sub)


# --------------------------------------------------------------------- Notes

class NotesPage(QWidget):
    """Notes — the `notes` tool's store, newest first, pinned first.

    Reads category "notes" ONLY (sibling categories never appear).
    The drawer holds the note KEY (D-P5-03); it re-reads by key on
    open and shows "This note was removed" when the key is gone
    (S-P5-06). Add calls the funnel's notes() add path; edit is
    save-new-then-delete-old with the key preserved (D-P5-04);
    remove is confirm-gated and permanent, with no Undo (D-P5-06).
    """

    def __init__(self, window=None, providers=None):
        super().__init__()
        self._win = window
        providers = providers or {}
        self._load = providers.get("load", _notes._load_notes)
        self._add = providers.get(
            "add", lambda **kw: _notes.notes(action="add", **kw))
        self._remove = providers.get("remove_by_key",
                                     _notes._remove_note_by_key)
        self._edit = providers.get("edit_by_key",
                                   _notes._edit_note_by_key)
        self._set_pinned = providers.get("set_pinned",
                                         _notes._set_note_pinned)
        self._entries = []
        self._broken = 0
        self._open_key = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        self._main = main
        root.addWidget(main, stretch=1)

        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setAccessibleName("Search notes")
        self._search.setPlaceholderText("Search notes")
        self._search.textChanged.connect(self._render_list)
        top.addWidget(self._search, stretch=1)
        self._sort_combo = QComboBox()
        self._sort_combo.setAccessibleName("Note order")
        self._sort_combo.addItem("Newest first")
        self._sort_combo.addItem("Recently edited")
        self._sort_combo.currentTextChanged.connect(self._render_list)
        top.addWidget(self._sort_combo)
        main_layout.addLayout(top)

        self._count_label = QLabel("")
        self._count_label.setObjectName("muted")
        main_layout.addWidget(self._count_label)

        self._private_label = QLabel(
            "Private mode is on — notes are hidden.")
        self._private_label.setObjectName("muted")
        self._private_label.setWordWrap(True)
        self._private_label.setVisible(False)
        main_layout.addWidget(self._private_label)

        self._notes_list = QListWidget()
        self._notes_list.setAccessibleName("Notes")
        self._notes_list.itemClicked.connect(self._on_row_clicked)
        main_layout.addWidget(self._notes_list, stretch=1)

        self._empty = None
        from .design_system.empty import EmptyState
        self._empty = EmptyState(
            sentence="No notes yet.",
            suggested_task="Take your first note — it stays on "
                           "this machine.",
            action_label="Take a note")
        self._empty.action_requested.connect(self._open_add)
        main_layout.addWidget(self._empty)

        self._broken_label = QLabel("")
        self._broken_label.setObjectName("muted")
        self._broken_label.setWordWrap(True)
        self._broken_label.setVisible(False)
        main_layout.addWidget(self._broken_label)

        self._add_btn = QPushButton("Take a note")
        self._add_btn.setObjectName("primaryBtn")
        self._add_btn.clicked.connect(self._open_add)
        main_layout.addWidget(self._add_btn, alignment=Qt.AlignLeft)

        from .design_system.drawer import ContextDrawer
        self._drawer = ContextDrawer(self, title="Note")
        self._drawer.setVisible(False)
        root.addWidget(self._drawer)

    # ------------------------------------------------------------ data
    def _ordered_entries(self):
        """Pinned first, then the sort order: newest first by key,
        or recently edited (edited stamp where one exists, else
        added) — labelled honestly by the sort combo."""
        entries = list(self._entries)
        if self._sort_combo.currentText() == "Recently edited":
            entries.sort(
                key=lambda e: e.get("edited") or e.get("added") or "",
                reverse=True)
        else:
            entries.sort(key=lambda e: e.get("key") or "", reverse=True)
        # Stable: pinned first, the sort order kept within groups.
        entries.sort(key=lambda e: not bool(e.get("pinned")))
        return entries

    def refresh(self):
        """Re-read the store (on show and after any write). A
        re-read, not a rebuild: the search text, the sort and the
        scroll position survive it."""
        # A drawer opened before private mode was turned on still
        # shows names built pre-private — close it on any refresh
        # while private is on. It cannot be reopened while
        # private (no open affordance shows), so nothing is lost
        # but the stale content.
        if _is_private():
            try:
                if self._drawer.isVisible():
                    _close_drawer(self._win, self._drawer, self._main)
            except Exception:  # noqa: BLE001 — refresh never breaks
                pass
        try:
            entries, broken = self._load()
        except Exception:  # noqa: BLE001 — a failed read reads empty
            entries, broken = [], 0
        self._entries = list(entries or [])
        self._broken = int(broken or 0)
        self._render_list()

    def _visible_entries(self):
        query = self._search.text().strip().lower()
        ordered = self._ordered_entries()
        if not query:
            return ordered, ordered
        shown = [e for e in ordered
                 if query in str(e.get("title") or "").lower()
                 or query in str(e.get("text") or "").lower()]
        return shown, ordered

    def _render_list(self):
        private = _is_private()
        self._private_label.setVisible(private)
        self._add_btn.setVisible(not private)
        self._empty.setVisible(False)
        shown, ordered = self._visible_entries()
        total = len(ordered)
        if private:
            self._notes_list.clear()
            self._notes_list.setVisible(False)
            self._count_label.setText(
                f"{total} note{'s' if total != 1 else ''}")
        else:
            self._notes_list.setVisible(True)
            self._notes_list.clear()
            for entry in shown:
                title = (entry.get("title") or
                         (entry.get("text") or "").splitlines()[0][:60])
                marker = "Pinned — " if entry.get("pinned") else ""
                item = QListWidgetItem(
                    f"{marker}{title}\n{entry.get('added') or ''}")
                item.setData(Qt.ItemDataRole.UserRole, entry.get("key"))
                self._notes_list.addItem(item)
            query = self._search.text().strip()
            if query:
                self._count_label.setText(
                    f"{len(shown)} of {total} notes")
            else:
                self._count_label.setText(
                    f"{total} note{'s' if total != 1 else ''}")
            if not ordered:
                self._empty.setVisible(True)
            elif not shown:
                self._empty.setVisible(True)
                self._empty.set_sentence(
                    f"No notes match '{query}'.")
            else:
                self._empty.set_sentence("No notes yet.")
        if self._broken:
            self._broken_label.setText(
                f"{self._broken} stored note"
                f"{'s' if self._broken != 1 else ''} couldn't be "
                f"read and {'were' if self._broken != 1 else 'was'} "
                "skipped.")
            self._broken_label.setVisible(True)
        else:
            self._broken_label.setVisible(False)

    # ------------------------------------------------------------ drawer
    def _drawer_body(self):
        layout = self._drawer.content_layout()
        _clear_layout(layout)
        return layout

    def _on_row_clicked(self, item):
        if _is_private():
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key:
            self._open_detail(str(key))

    def _open_detail(self, key):
        """Re-read by KEY on open (S-P5-06): a missing key shows
        'This note was removed', never an empty editor."""
        if _is_private():
            return
        try:
            entries, _b = self._load()
        except Exception:  # noqa: BLE001
            entries = []
        entry = next((e for e in (entries or [])
                      if e.get("key") == key), None)
        self._open_key = key
        layout = self._drawer_body()
        self._drawer._title.setText("Note")
        if entry is None:
            gone = QLabel("This note was removed.")
            gone.setWordWrap(True)
            layout.addWidget(gone)
            close = _chip("Close")
            close.clicked.connect(
                lambda: _close_drawer(self._win, self._drawer, self._main))
            layout.addWidget(close)
            _open_drawer(self._win, self._drawer, self._main)
            return
        title = _row_title(entry.get("title") or "(No title)")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        body = QLabel(entry.get("text") or "")
        body.setWordWrap(True)
        layout.addWidget(body)
        dates = f"Added {entry.get('added') or '—'}"
        if entry.get("edited"):
            dates += f" · Edited {entry['edited']}"
        layout.addWidget(_row_sub(dates))
        pin = _chip("Unpin" if entry.get("pinned") else "Pin")
        pin.setCheckable(True)
        pin.setChecked(bool(entry.get("pinned")))
        pin.clicked.connect(
            lambda _c=False, k=key: self._on_pin_toggled(k))
        layout.addWidget(pin)
        row = QHBoxLayout()
        edit = _chip("Edit")
        edit.clicked.connect(
            lambda _c=False, k=key: self._open_edit(k))
        row.addWidget(edit)
        remove = _chip("Remove")
        remove.clicked.connect(
            lambda _c=False, k=key: self._on_remove(k))
        row.addWidget(remove)
        close = _chip("Close")
        close.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        row.addWidget(close)
        row.addStretch()
        layout.addLayout(row)
        _open_drawer(self._win, self._drawer, self._main)

    def _on_pin_toggled(self, key):
        try:
            entries, _b = self._load()
        except Exception:  # noqa: BLE001
            entries = []
        entry = next((e for e in (entries or [])
                      if e.get("key") == key), None)
        if entry is None:
            return
        ok, message = self._set_pinned(key, not bool(entry.get("pinned")))
        if ok:
            self.refresh()
            self._open_detail(key)
            _call_win(self._win, "_views_toast", message)
        else:
            _call_win(self._win, "_views_toast", message)

    def _open_edit(self, key):
        if _is_private():
            return
        try:
            entries, _b = self._load()
        except Exception:  # noqa: BLE001
            entries = []
        entry = next((e for e in (entries or [])
                      if e.get("key") == key), None)
        layout = self._drawer_body()
        self._drawer._title.setText("Edit note")
        if entry is None:
            gone = QLabel("This note was removed.")
            gone.setWordWrap(True)
            layout.addWidget(gone)
            _open_drawer(self._win, self._drawer, self._main)
            return
        title_edit = QLineEdit(entry.get("title") or "")
        title_edit.setPlaceholderText("Title (optional)")
        title_edit.setMaxLength(100)
        layout.addWidget(title_edit)
        body_edit = QPlainTextEdit(entry.get("text") or "")
        body_edit.setPlaceholderText("Note")
        layout.addWidget(body_edit)
        counter = QLabel("")
        counter.setObjectName("muted")
        layout.addWidget(counter)

        def _count():
            collapsed = " ".join(body_edit.toPlainText().split())
            counter.setText(f"{len(collapsed)} / 2000")
            return len(collapsed)

        body_edit.textChanged.connect(_count)
        _count()
        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        save = _chip("Save")
        save.setObjectName("primaryBtn")

        def _save(_c=False, k=key):
            if _count() == 0:
                status.setText("That needs the note's text.")
                return
            if _count() > 2000:
                status.setText(
                    "That note is over 2000 characters — shorten "
                    "it to save.")
                return
            ok, message = self._edit(
                k, title_edit.text(), body_edit.toPlainText())
            if ok:
                self.refresh()
                self._open_detail(k)
                _call_win(self._win, "_views_toast", message)
            else:
                # A failed save leaves the old note untouched.
                status.setText(message)

        save.clicked.connect(_save)
        row.addWidget(save)
        cancel = _chip("Cancel")
        cancel.clicked.connect(
            lambda: self._open_detail(key))
        row.addWidget(cancel)
        row.addStretch()
        layout.addLayout(row)
        _open_drawer(self._win, self._drawer, self._main)

    def _open_add(self):
        """Add drawer: title (optional) + body, 2000-char cap
        shown as 'N / 2000'. Save calls the funnel's notes() add
        path; the list re-reads before the save is claimed."""
        if _is_private():
            return
        layout = self._drawer_body()
        self._drawer._title.setText("Take a note")
        title_edit = QLineEdit()
        title_edit.setPlaceholderText("Title (optional)")
        title_edit.setMaxLength(100)
        layout.addWidget(title_edit)
        body_edit = QPlainTextEdit()
        body_edit.setPlaceholderText("Note")
        layout.addWidget(body_edit)
        counter = QLabel("0 / 2000")
        counter.setObjectName("muted")
        layout.addWidget(counter)

        def _count():
            collapsed = " ".join(body_edit.toPlainText().split())
            counter.setText(f"{len(collapsed)} / 2000")
            return len(collapsed)

        body_edit.textChanged.connect(_count)
        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        save = _chip("Save note")
        save.setObjectName("primaryBtn")

        def _save(_c=False):
            if _count() == 0:
                status.setText("That needs the note's text.")
                return
            if _count() > 2000:
                status.setText(
                    "That note is over 2000 characters — shorten "
                    "it to save.")
                return
            answer = self._add(text=body_edit.toPlainText(),
                               title=title_edit.text())
            if str(answer or "").startswith("Noted:"):
                self.refresh()
                _close_drawer(self._win, self._drawer, self._main)
                _call_win(self._win, "_views_toast", str(answer))
            else:
                status.setText(str(answer or "Couldn't save the note."))

        save.clicked.connect(_save)
        row.addWidget(save)
        cancel = _chip("Cancel")
        cancel.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        row.addWidget(cancel)
        row.addStretch()
        layout.addLayout(row)
        _open_drawer(self._win, self._drawer, self._main)

    def _on_remove(self, key):
        """Inline approval card naming the title, saying the
        remove is permanent. No Undo afterwards (delete is
        UNDO_FORBIDDEN). Operates by KEY, never by number."""
        try:
            entries, _b = self._load()
        except Exception:  # noqa: BLE001
            entries = []
        entry = next((e for e in (entries or [])
                      if e.get("key") == key), None)
        if entry is None:
            _close_drawer(self._win, self._drawer, self._main)
            return
        title = entry.get("title") or (entry.get("text") or "")[:40]
        allowed = _call_win(
            self._win, "_views_confirm_write", "notes", title,
            f"Remove '{title}' permanently? This can't be undone.",
            default=False)
        if not allowed:
            return
        ok, message = self._remove(key)
        _close_drawer(self._win, self._drawer, self._main)
        self.refresh()
        _call_win(self._win, "_views_toast", message)


# ------------------------------------------------------------------- Finance

class FinancePage(QWidget):
    """Finance — the verified local money stores only, every figure
    labelled with its source. The unsupported state is the FIRST
    element and stays even when sections below have data; there is
    no bank/account/balance/transaction/payment surface of any
    kind (HELD), and no "Connect a bank" button — nothing exists
    to connect to. Calculators are launcher rows to the Skills
    centre (never re-implemented); the quote plugins are NEVER
    wired in, so a holding without a recorded price is unvalued,
    never guessed."""

    def __init__(self, window=None, providers=None):
        super().__init__()
        self._win = window
        providers = providers or {}
        self._load_env = providers.get("load_envelopes",
                                       _money._load_envelopes)
        self._load_sub = providers.get("load_subscriptions",
                                       _money._load_subscriptions)
        self._load_port = providers.get("load_portfolio",
                                        _money._load_portfolio)
        self._envelope = providers.get(
            "envelope", lambda **kw: _money.budget_envelope(**kw))
        self._subscription = providers.get(
            "subscription", lambda **kw: _money.subscription_audit(**kw))
        self._portfolio = providers.get(
            "portfolio", lambda **kw: _money.portfolio_tracker(**kw))
        self._standing = providers.get("standing_alerts",
                                       _alerts.money_alert_lines)
        self._check = providers.get("check_alerts",
                                    _alerts.check_money_alerts)
        self._next_renewal = providers.get("next_renewal",
                                           _money._next_renewal)
        self._env_entries = []
        self._env_broken = 0
        self._sub_entries = []
        self._sub_broken = 0
        self._port_entries = []
        self._port_broken = 0
        self._standing_lines = []
        self._due_lines = []
        self._due_failures = []

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        self._main = main
        root.addWidget(main, stretch=1)

        # First element, always: the unsupported state, named.
        unsup, unsup_lay = _card_row(main_layout)
        unsup_title = _row_title("No bank or account connected")
        unsup_title.setObjectName("sectionTitle")
        unsup_lay.addWidget(unsup_title)
        unsup_lay.addWidget(_row_sub(
            "VYUK has no way to read your accounts, and this page "
            "never pretends otherwise."))
        self._unsupported_card = unsup

        from .design_system.tabs import HudTabs
        self._tabs = HudTabs()
        for name in ("Envelopes", "Subscriptions", "Portfolio",
                     "Alerts"):
            self._tabs.addTab(name)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        main_layout.addWidget(self._tabs)
        self._stack = QStackedWidget()
        main_layout.addWidget(self._stack, stretch=1)
        self._tab_pages = {}
        self._tab_rows = {}
        self._tab_totals = {}
        self._tab_broken = {}
        self._tab_empty = {}
        self._tab_private = {}
        self._tab_add = {}
        for name in ("Envelopes", "Subscriptions", "Portfolio",
                     "Alerts"):
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(8)
            source = QLabel("You recorded this.")
            source.setObjectName("muted")
            lay.addWidget(source)
            private = QLabel(
                "Private mode is on — figures are hidden.")
            private.setObjectName("muted")
            private.setWordWrap(True)
            private.setVisible(False)
            lay.addWidget(private)
            rows = QVBoxLayout()
            rows.setSpacing(8)
            lay.addLayout(rows)
            totals = QLabel("")
            totals.setObjectName("muted")
            totals.setWordWrap(True)
            lay.addWidget(totals)
            broken = QLabel("")
            broken.setObjectName("muted")
            broken.setWordWrap(True)
            broken.setVisible(False)
            lay.addWidget(broken)
            from .design_system.empty import EmptyState
            empty = EmptyState(sentence="", suggested_task="")
            lay.addWidget(empty)
            lay.addStretch()
            self._stack.addWidget(page)
            self._tab_pages[name] = page
            self._tab_rows[name] = rows
            self._tab_totals[name] = totals
            self._tab_broken[name] = broken
            self._tab_empty[name] = empty
            self._tab_private[name] = private
        self._tab_empty["Envelopes"].set_sentence(
            "No envelopes yet.")
        self._tab_empty["Subscriptions"].set_sentence(
            "No subscriptions recorded yet.")
        self._tab_empty["Portfolio"].set_sentence(
            "No holdings recorded yet.")
        self._tab_empty["Alerts"].set_sentence(
            "No money alerts right now.")
        # Per-tab add buttons (write actions, hidden in private mode).
        self._tab_add["Envelopes"] = _chip("Set an envelope")
        self._tab_add["Envelopes"].clicked.connect(
            lambda: self._open_envelope_set(None))
        self._tab_pages["Envelopes"].layout().insertWidget(
            2, self._tab_add["Envelopes"])
        self._tab_add["Subscriptions"] = _chip("Add a subscription")
        self._tab_add["Subscriptions"].clicked.connect(
            self._open_subscription_add)
        self._tab_pages["Subscriptions"].layout().insertWidget(
            2, self._tab_add["Subscriptions"])
        self._tab_add["Portfolio"] = _chip("Add a holding")
        self._tab_add["Portfolio"].clicked.connect(
            self._open_portfolio_add)
        self._tab_pages["Portfolio"].layout().insertWidget(
            2, self._tab_add["Portfolio"])
        check_btn = _chip("Check for new alerts")
        check_btn.clicked.connect(self._on_check_alerts)
        self._tab_add["Alerts"] = check_btn
        self._tab_pages["Alerts"].layout().insertWidget(
            2, self._tab_add["Alerts"])

        # Calculators: launcher rows to the Skills centre.
        calc, calc_lay = _card_row(main_layout)
        calc_title = _row_title("Calculators")
        calc_title.setObjectName("sectionTitle")
        calc_lay.addWidget(calc_title)
        calc_lay.addWidget(_row_sub(
            "Illustration of the maths, not a prediction and not "
            "financial advice. Nothing here is stored as a fact "
            "or presented as a balance."))
        for label, tool in _CALCULATORS:
            btn = _chip(label)
            btn.clicked.connect(
                lambda _c=False, t=tool: _call_win(
                    self._win, "_open_palette_with", t))
            calc_lay.addWidget(btn)
        self._calc_card = calc

        self._cost_btn = _chip("What VYUK's brain has cost you")
        self._cost_btn.clicked.connect(
            lambda: _call_win(self._win, "_open_cost_ledger"))
        main_layout.addWidget(self._cost_btn,
                              alignment=Qt.AlignLeft)

        from .design_system.drawer import ContextDrawer
        self._drawer = ContextDrawer(self, title="Finance")
        self._drawer.setVisible(False)
        root.addWidget(self._drawer)

    # ------------------------------------------------------------ data
    def refresh(self):
        """Re-read every local store (on show and after any write).
        A failed read in one store shows that tab's broken line
        and never blanks the other tabs (S-P5-04)."""
        # A drawer opened before private mode was turned on still
        # shows figures built pre-private — close it on any
        # refresh while private is on (see NotesPage.refresh).
        if _is_private():
            try:
                if self._drawer.isVisible():
                    _close_drawer(self._win, self._drawer, self._main)
            except Exception:  # noqa: BLE001 — refresh never breaks
                pass
        try:
            self._env_entries, self._env_broken = self._load_env()
        except Exception:  # noqa: BLE001
            self._env_entries, self._env_broken = [], 0
        try:
            self._sub_entries, self._sub_broken = self._load_sub()
        except Exception:  # noqa: BLE001
            self._sub_entries, self._sub_broken = [], 0
        try:
            self._port_entries, self._port_broken = self._load_port()
        except Exception:  # noqa: BLE001
            self._port_entries, self._port_broken = [], 0
        try:
            self._standing_lines = list(self._standing() or [])
        except Exception:  # noqa: BLE001
            self._standing_lines = []
        self._render_all()

    def _on_tab_changed(self, _index):
        self._stack.setCurrentIndex(_index)

    def _render_all(self):
        private = _is_private()
        for name in ("Envelopes", "Subscriptions", "Portfolio",
                     "Alerts"):
            self._tab_private[name].setVisible(private)
            add = self._tab_add.get(name)
            if add is not None:
                add.setVisible(not private)
        self._cost_btn.setVisible(not private)
        self._render_envelopes(private)
        self._render_subscriptions(private)
        self._render_portfolio(private)
        self._render_alerts(private)

    def _broken_line(self, label, broken, noun):
        if broken:
            label.setText(
                f"{broken} stored {noun}"
                f"{'s' if broken != 1 else ''} couldn't be read "
                "and were skipped.")
            label.setVisible(True)
        else:
            label.setVisible(False)

    def _render_envelopes(self, private):
        rows = self._tab_rows["Envelopes"]
        _clear_layout(rows)
        entries = list(self._env_entries or [])
        if private:
            self._tab_totals["Envelopes"].setText(
                f"{len(entries)} envelope"
                f"{'s' if len(entries) != 1 else ''}")
            self._tab_empty["Envelopes"].setVisible(False)
            self._broken_line(self._tab_broken["Envelopes"],
                              self._env_broken, "envelope")
            return
        for entry in entries:
            budget = entry["budget"]
            spent = entry["spent"]
            pct = (spent / budget * 100.0) if budget > 0 else 0.0
            card, lay = _card_row(rows)
            head = QHBoxLayout()
            head.addWidget(_row_title(entry["key"]))
            head.addStretch()
            from .design_system.pills import StatusPill
            pill = StatusPill(
                text=("Over budget" if spent > budget
                      else "Within budget"),
                severity=("danger" if spent > budget else "success"))
            head.addWidget(pill)
            lay.addLayout(head)
            left = (_fmt(budget - spent, _RUPEE) + " left"
                    if spent <= budget else
                    _fmt(spent - budget, _RUPEE) + " OVER budget")
            lay.addWidget(_row_sub(
                f"Budget {_fmt(budget, _RUPEE)}, spent "
                f"{_fmt(spent, _RUPEE)} since set — {left} "
                f"({pct:.0f}% used)"))
            btns = QHBoxLayout()
            spend = _chip("Add spending")
            spend.clicked.connect(
                lambda _c=False, k=entry["key"]:
                    self._open_envelope_spend(k))
            btns.addWidget(spend)
            reset = _chip("Change budget")
            reset.clicked.connect(
                lambda _c=False, k=entry["key"]:
                    self._open_envelope_set(k))
            btns.addWidget(reset)
            btns.addStretch()
            lay.addLayout(btns)
        total_b = sum(e["budget"] for e in entries)
        total_s = sum(e["spent"] for e in entries)
        self._tab_totals["Envelopes"].setText(
            f"Totals: budgets {_fmt(total_b, _RUPEE)}, spent "
            f"{_fmt(total_s, _RUPEE)}.")
        self._tab_empty["Envelopes"].setVisible(not entries)
        self._broken_line(self._tab_broken["Envelopes"],
                          self._env_broken, "envelope")

    def _render_subscriptions(self, private):
        rows = self._tab_rows["Subscriptions"]
        _clear_layout(rows)
        entries = list(self._sub_entries or [])
        if private:
            self._tab_totals["Subscriptions"].setText(
                f"{len(entries)} subscription"
                f"{'s' if len(entries) != 1 else ''}")
            self._tab_empty["Subscriptions"].setVisible(False)
            self._broken_line(self._tab_broken["Subscriptions"],
                              self._sub_broken, "subscription")
            return
        import datetime as _dt
        today = _dt.date.today()
        for entry in entries:
            card, lay = _card_row(rows)
            lay.addWidget(_row_title(entry["key"]))
            line = (f"{_fmt(entry['monthly'], _RUPEE)} a month "
                    f"({_fmt(entry['monthly'] * 12, _RUPEE)} a year)")
            if entry.get("renewal_day"):
                nxt = self._next_renewal(entry["renewal_day"], today)
                if nxt is not None:
                    line += f", renews {nxt.isoformat()}"
            lay.addWidget(_row_sub(line))
            btns = QHBoxLayout()
            remove = _chip("Remove")
            remove.clicked.connect(
                lambda _c=False, k=entry["key"]:
                    self._on_subscription_remove(k))
            btns.addWidget(remove)
            btns.addStretch()
            lay.addLayout(btns)
        total_m = sum(e["monthly"] for e in entries)
        biggest = (f"The biggest is '{entries[0]['key']}' at "
                   f"{_fmt(entries[0]['monthly'], _RUPEE)} a month."
                   if entries else "")
        self._tab_totals["Subscriptions"].setText(
            f"Total: {_fmt(total_m, _RUPEE)} a month, "
            f"{_fmt(total_m * 12, _RUPEE)} a year. {biggest}")
        self._tab_empty["Subscriptions"].setVisible(not entries)
        self._broken_line(self._tab_broken["Subscriptions"],
                          self._sub_broken, "subscription")

    def _render_portfolio(self, private):
        rows = self._tab_rows["Portfolio"]
        _clear_layout(rows)
        entries = list(self._port_entries or [])
        if private:
            self._tab_totals["Portfolio"].setText(
                f"{len(entries)} holding"
                f"{'s' if len(entries) != 1 else ''}")
            self._tab_empty["Portfolio"].setVisible(False)
            self._broken_line(self._tab_broken["Portfolio"],
                              self._port_broken, "holding")
            return
        for entry in entries:
            card, lay = _card_row(rows)
            lay.addWidget(_row_title(entry["key"]))
            bits = [f"{entry['quantity']:g} held"]
            if entry.get("buy_price") is not None:
                bits.append(
                    f"cost {_fmt(entry['buy_price'], _RUPEE)} each")
            if entry.get("price") is not None:
                bits.append(
                    f"recorded price {_fmt(entry['price'], _RUPEE)} "
                    f"each, value "
                    f"{_fmt(entry['quantity'] * entry['price'], _RUPEE)}")
            else:
                bits.append("No price recorded — live quotes are off")
            lay.addWidget(_row_sub(", ".join(bits) + "."))
            btns = QHBoxLayout()
            remove = _chip("Remove")
            remove.clicked.connect(
                lambda _c=False, k=entry["key"]:
                    self._on_portfolio_remove(k))
            btns.addWidget(remove)
            btns.addStretch()
            lay.addLayout(btns)
        invested = sum(e["quantity"] * e["buy_price"] for e in entries
                       if e.get("buy_price") is not None)
        current = sum(e["quantity"] * e["price"] for e in entries
                      if e.get("price") is not None)
        unvalued = [e["key"] for e in entries
                    if e.get("price") is None]
        lines = []
        if any(e.get("buy_price") is not None for e in entries):
            lines.append(
                f"Invested: {_fmt(invested, _RUPEE)} (from the cost "
                "prices you recorded).")
        if any(e.get("price") is not None for e in entries):
            lines.append(
                f"Current value: {_fmt(current, _RUPEE)} (from the "
                "prices you recorded).")
        if unvalued:
            lines.append(
                f"Unvalued: {', '.join(unvalued)} — no recorded "
                "price, and live quotes are off.")
        lines.append("No advice, no trading.")
        self._tab_totals["Portfolio"].setText(" ".join(lines))
        self._tab_empty["Portfolio"].setVisible(not entries)
        self._broken_line(self._tab_broken["Portfolio"],
                          self._port_broken, "holding")

    def _render_alerts(self, private):
        rows = self._tab_rows["Alerts"]
        _clear_layout(rows)
        standing = list(self._standing_lines or [])
        due = list(self._due_lines or [])
        failures = list(self._due_failures or [])
        if private:
            self._tab_totals["Alerts"].setText(
                f"{len(standing)} standing alert"
                f"{'s' if len(standing) != 1 else ''}")
            self._tab_empty["Alerts"].setVisible(False)
            return
        if standing:
            rows.addWidget(_row_title("Standing"))
            for line in standing:
                rows.addWidget(_row_sub(line))
        if due:
            rows.addWidget(_row_title("Due now"))
            for line in due:
                rows.addWidget(_row_sub(line))
        if failures:
            rows.addWidget(_row_sub(
                "Checks that couldn't run: " + "; ".join(failures)))
        self._tab_totals["Alerts"].setText(
            f"{len(standing)} standing, {len(due)} due now.")
        self._tab_empty["Alerts"].setVisible(
            not standing and not due)

    def _on_check_alerts(self):
        """Newly-due alerts, on demand (the check may fetch price
        pages — that is the tool's own behaviour, and it runs only
        when the user asks)."""
        try:
            answer = str(self._check() or "")
        except Exception:  # noqa: BLE001
            answer = ""
        due, failures = [], []
        section = None
        for line in answer.splitlines():
            line = line.strip()
            if line.startswith("Money alerts"):
                section = "due"
                continue
            if line.startswith("No money alerts"):
                section = None
                continue
            if line.startswith("Checks that couldn't run"):
                section = "failed"
                continue
            if line.startswith("• "):
                if section == "due":
                    due.append(line[2:])
                elif section == "failed":
                    failures.append(line[2:])
        self._due_lines = due
        self._due_failures = failures
        self._render_alerts(_is_private())

    # ------------------------------------------------------------ drawer
    def _drawer_body(self, title):
        layout = self._drawer.content_layout()
        _clear_layout(layout)
        self._drawer._title.setText(title)
        return layout

    def _number_field(self, layout, placeholder):
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setInputMethodHints(Qt.InputMethodHint.ImhFormattedNumbersOnly)
        layout.addWidget(field)
        return field

    def _parse_amount(self, text):
        try:
            value = float(str(text or "").strip().replace(",", ""))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _open_envelope_set(self, existing):
        if _is_private():
            return
        layout = self._drawer_body("Set an envelope")
        name_edit = QLineEdit(existing or "")
        name_edit.setPlaceholderText("Envelope name")
        if existing:
            name_edit.setEnabled(False)
        layout.addWidget(name_edit)
        budget_edit = self._number_field(layout, "Budget amount")
        layout.addWidget(_row_sub(
            "Setting an envelope resets its spent to zero."))
        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        save = _chip("Save envelope")
        save.setObjectName("primaryBtn")

        def _save(_c=False):
            name = name_edit.text().strip()
            if not name:
                status.setText("That needs the envelope's name.")
                return
            amount = self._parse_amount(budget_edit.text())
            if amount is None:
                status.setText("The budget needs to be a number "
                               "more than zero.")
                return
            answer = str(self._envelope(
                action="set", name=name, amount=amount) or "")
            self.refresh()
            found = next((e for e in self._env_entries
                          if e["key"].strip().lower() == name.lower()
                          and e["budget"] == amount
                          and e["spent"] == 0.0), None)
            if found is not None:
                _close_drawer(self._win, self._drawer, self._main)
                _call_win(self._win, "_views_toast", answer)
            else:
                status.setText(answer or "Couldn't save the envelope.")

        save.clicked.connect(_save)
        row.addWidget(save)
        cancel = _chip("Cancel")
        cancel.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        row.addWidget(cancel)
        row.addStretch()
        layout.addLayout(row)
        _open_drawer(self._win, self._drawer, self._main)

    def _open_envelope_spend(self, name):
        if _is_private():
            return
        layout = self._drawer_body("Add spending")
        layout.addWidget(_row_sub(f"Envelope: {name}"))
        amount_edit = self._number_field(layout, "Amount spent")
        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        save = _chip("Add spending")
        save.setObjectName("primaryBtn")

        def _save(_c=False):
            amount = self._parse_amount(amount_edit.text())
            if amount is None:
                status.setText("The amount needs to be a number "
                               "more than zero.")
                return
            before = next((e["spent"] for e in self._env_entries
                           if e["key"] == name), None)
            answer = str(self._envelope(
                action="spend", name=name, amount=amount) or "")
            self.refresh()
            after = next((e["spent"] for e in self._env_entries
                          if e["key"] == name), None)
            if (before is not None and after is not None
                    and abs(after - (before + amount)) < 1e-9):
                _close_drawer(self._win, self._drawer, self._main)
                _call_win(self._win, "_views_toast", answer)
            else:
                status.setText(answer or "Couldn't record the spending.")

        save.clicked.connect(_save)
        row.addWidget(save)
        cancel = _chip("Cancel")
        cancel.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        row.addWidget(cancel)
        row.addStretch()
        layout.addLayout(row)
        _open_drawer(self._win, self._drawer, self._main)

    def _open_subscription_add(self):
        if _is_private():
            return
        layout = self._drawer_body("Add a subscription")
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("Subscription name")
        layout.addWidget(name_edit)
        monthly_edit = self._number_field(layout, "Monthly cost")
        day_edit = self._number_field(
            layout, "Renewal day of the month (optional, 1–31)")
        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        save = _chip("Save subscription")
        save.setObjectName("primaryBtn")

        def _save(_c=False):
            name = name_edit.text().strip()
            if not name:
                status.setText("That needs the subscription's name.")
                return
            monthly = self._parse_amount(monthly_edit.text())
            if monthly is None:
                status.setText("The monthly cost needs to be a "
                               "number more than zero.")
                return
            day = None
            if day_edit.text().strip():
                try:
                    day = int(day_edit.text().strip())
                except ValueError:
                    day = None
                if day is None or not 1 <= day <= 31:
                    status.setText("The renewal day has to be a day "
                                   "of the month, 1 to 31.")
                    return
            answer = str(self._subscription(
                action="add", name=name, monthly=monthly,
                renewal_day=day or 0) or "")
            self.refresh()
            found = next((e for e in self._sub_entries
                          if e["key"].strip().lower() == name.lower()
                          and e["monthly"] == monthly), None)
            if found is not None:
                _close_drawer(self._win, self._drawer, self._main)
                _call_win(self._win, "_views_toast", answer)
            else:
                status.setText(answer or "Couldn't save the "
                                        "subscription.")

        save.clicked.connect(_save)
        row.addWidget(save)
        cancel = _chip("Cancel")
        cancel.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        row.addWidget(cancel)
        row.addStretch()
        layout.addLayout(row)
        _open_drawer(self._win, self._drawer, self._main)

    def _on_subscription_remove(self, name):
        allowed = _call_win(
            self._win, "_views_confirm_write", "subscription_audit",
            name,
            f"Remove the '{name}' subscription permanently? This "
            "can't be undone.",
            default=False)
        if not allowed:
            return
        answer = str(self._subscription(action="remove", name=name) or "")
        self.refresh()
        gone = all(e["key"] != name for e in self._sub_entries)
        _call_win(self._win, "_views_toast",
                  answer if gone else "Couldn't remove the subscription.")

    def _open_portfolio_add(self):
        if _is_private():
            return
        layout = self._drawer_body("Add a holding")
        symbol_edit = QLineEdit()
        symbol_edit.setPlaceholderText("Symbol, like INFY")
        layout.addWidget(symbol_edit)
        qty_edit = self._number_field(layout, "Quantity")
        buy_edit = self._number_field(
            layout, "Cost price per unit (optional)")
        price_edit = self._number_field(
            layout, "Recorded current price per unit (optional)")
        layout.addWidget(_row_sub(
            "A holding with no recorded price is unvalued — live "
            "quotes are off, and no price is ever guessed."))
        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        save = _chip("Save holding")
        save.setObjectName("primaryBtn")

        def _save(_c=False):
            symbol = symbol_edit.text().strip().upper()
            if not symbol:
                status.setText("That needs the holding's symbol, "
                               "like 'INFY'.")
                return
            qty = self._parse_amount(qty_edit.text())
            if qty is None:
                status.setText("The quantity needs to be a number "
                               "more than zero.")
                return
            buy = self._parse_amount(buy_edit.text()) or 0
            price = self._parse_amount(price_edit.text()) or 0
            answer = str(self._portfolio(
                action="add", symbol=symbol, quantity=qty,
                buy_price=buy, price=price) or "")
            self.refresh()
            found = next((e for e in self._port_entries
                          if e["key"] == symbol), None)
            if found is not None and found["quantity"] >= qty:
                _close_drawer(self._win, self._drawer, self._main)
                _call_win(self._win, "_views_toast", answer)
            else:
                status.setText(answer or "Couldn't save the holding.")

        save.clicked.connect(_save)
        row.addWidget(save)
        cancel = _chip("Cancel")
        cancel.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        row.addWidget(cancel)
        row.addStretch()
        layout.addLayout(row)
        _open_drawer(self._win, self._drawer, self._main)

    def _on_portfolio_remove(self, symbol):
        allowed = _call_win(
            self._win, "_views_confirm_write", "portfolio_tracker",
            symbol,
            f"Remove the '{symbol}' holding permanently? This "
            "can't be undone.",
            default=False)
        if not allowed:
            return
        answer = str(self._portfolio(action="remove", symbol=symbol) or "")
        self.refresh()
        gone = all(e["key"] != symbol for e in self._port_entries)
        _call_win(self._win, "_views_toast",
                  answer if gone else "Couldn't remove the holding.")


# ------------------------------------------------------------------- Devices

class DevicesPage(QWidget):
    """Devices / Smart Home — the Home Assistant client, dormant by
    default. Not-configured first (D10 honest-empty); connected
    reads and controls only after a configured instance is verified
    (lab: loopback fake hub; field: real hub — owed). No WebSocket,
    no timer, no polling: manual refresh + on-show re-read only.
    Connection state comes from a live health() call, never a
    stored flag (D-P5-16); a failed re-read keeps the last good
    states with a "Last updated …" stamp (S-P5-05)."""

    def __init__(self, window=None, providers=None):
        super().__init__()
        self._win = window
        providers = providers or {}
        self._load_config = providers.get("load_config",
                                          _hac.load_ha_config)
        self._build_client = providers.get("build_client",
                                           _hac.build_ha_client)
        self._has_grant = providers.get(
            "has_grant",
            lambda: bool(_grants.has_session_grant("home_devices")))
        self._error_line = providers.get("error_line",
                                         _hac.ha_error_line)
        self._join = providers.get("join", _ha.join_snapshot)
        self._snapshot = None
        self._last_good_at = None
        self._client = None
        self._config = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        main = QWidget()
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(8)
        self._main = main
        root.addWidget(main, stretch=1)

        top = QHBoxLayout()
        self._refresh_btn = _chip("Refresh")
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        self._stale_label = QLabel("")
        self._stale_label.setObjectName("muted")
        self._stale_label.setWordWrap(True)
        top.addWidget(self._stale_label, stretch=1)
        main_layout.addLayout(top)

        self._private_label = QLabel(
            "Private mode is on — device and room names are hidden.")
        self._private_label.setObjectName("muted")
        self._private_label.setWordWrap(True)
        self._private_label.setVisible(False)
        main_layout.addWidget(self._private_label)

        self._body = QVBoxLayout()
        self._body.setSpacing(8)
        main_layout.addLayout(self._body)
        main_layout.addStretch()

        from .design_system.drawer import ContextDrawer
        self._drawer = ContextDrawer(self, title="Device")
        self._drawer.setVisible(False)
        root.addWidget(self._drawer)

    # ------------------------------------------------------------ data
    def refresh(self):
        """On show and on manual refresh only (no timer). A re-read,
        never a rebuild — and in private mode no hub read at all."""
        if _is_private():
            # A drawer opened before private mode was turned on
            # still shows names built pre-private — close it
            # (see NotesPage.refresh). The close runs AFTER the
            # private render: clearing the body while the drawer
            # is still open is the shape that exits clean (the
            # close-first order aborted on interpreter exit in
            # the full test_ui_p5 suite — malloc_consolidate,
            # rc=134, tests OK; reorder verified rc=0). No paint
            # happens mid-refresh, so the final state is identical.
            self._render_private()
            try:
                if self._drawer.isVisible():
                    _close_drawer(self._win, self._drawer, self._main)
            except Exception:  # noqa: BLE001 — refresh never breaks
                pass
            return
        self._private_label.setVisible(False)
        self._refresh_btn.setVisible(True)
        try:
            cfg = self._load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
        self._config = dict(cfg or {})
        if not self._config.get("configured"):
            self._client = None
            self._render_not_configured()
            return
        try:
            granted = bool(self._has_grant())
        except Exception:  # noqa: BLE001 — a failed read reads as no grant
            granted = False
        if not granted:
            self._client = None
            self._render_no_permission()
            return
        client, reason = self._build_client()
        if client is None:
            self._render_error(
                reason or "The hub couldn't be reached.")
            return
        self._client = client
        try:
            client.health()
            areas = client.get_areas()
            devices = client.get_devices()
            states = client.get_states()
        except Exception as err:  # noqa: BLE001 — keep the last good
            self._render_error(self._error_line(err))
            return
        import datetime as _dt
        self._snapshot = self._join(areas, devices, states)
        self._last_good_at = _dt.datetime.now().astimezone()
        self._render_connected()

    def _render_private(self):
        _clear_layout(self._body)
        self._private_label.setVisible(True)
        self._refresh_btn.setVisible(False)
        self._stale_label.setText("")
        snap = self._snapshot or {}
        rooms = len(snap.get("rooms") or [])
        devices = sum(len(r.get("devices") or [])
                      for r in (snap.get("rooms") or []))
        devices += len(snap.get("unassigned_devices") or [])
        if self._config.get("configured"):
            self._body.addWidget(_row_sub(
                f"{rooms} room{'s' if rooms != 1 else ''}, "
                f"{devices} device{'s' if devices != 1 else ''} — "
                "names hidden while private mode is on."))
        else:
            self._body.addWidget(_row_sub(
                "No home hub connected yet."))

    def _render_not_configured(self):
        _clear_layout(self._body)
        self._stale_label.setText("")
        from .design_system.empty import EmptyState
        empty = EmptyState(
            sentence="No home hub connected yet.",
            suggested_task="A hub is a Home Assistant instance on "
                           "your network. Set one up to see your "
                           "rooms and devices.",
            action_label="Set up a hub")
        empty.action_requested.connect(
            lambda: _call_win(self._win, "show_settings_card",
                              "Home hub"))
        if _is_private() and empty._button is not None:
            empty._button.setVisible(False)
        self._body.addWidget(empty)

    def _render_no_permission(self):
        _clear_layout(self._body)
        self._stale_label.setText("")
        card, lay = _card_row(self._body)
        title = _row_title("Home devices need your permission")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)
        lay.addWidget(_row_sub(
            "A hub is configured, but VYUK has no permission to use "
            "your home devices yet. Grant it in Settings → "
            "Permissions — configuring a hub and granting the "
            "permission are two different steps."))
        btn = _chip("Open Permissions")
        btn.clicked.connect(
            lambda: _call_win(self._win, "show_settings_card",
                              "Permissions"))
        lay.addWidget(btn)

    def _render_error(self, line):
        """A failed re-read keeps the last good states (S-P5-05)
        with a 'Last updated …' stamp; with no last good, the
        error stands alone with a way back."""
        self._stale_label.setText("")
        if self._snapshot is not None:
            self._render_connected()
            self._stale_label.setText(
                (f"Last updated "
                 f"{self._last_good_at.strftime('%H:%M')} — showing "
                 "the last states the hub confirmed. " + line)
                if self._last_good_at else line)
            return
        _clear_layout(self._body)
        card, lay = _card_row(self._body)
        title = _row_title("Couldn't reach the hub")
        title.setObjectName("sectionTitle")
        lay.addWidget(title)
        lay.addWidget(_row_sub(line))
        retry = _chip("Try again")
        retry.clicked.connect(self.refresh)
        lay.addWidget(retry)

    def _pill_for(self, state_text):
        from .design_system.pills import StatusPill
        severity = {"on": "success", "off": "neutral",
                    "Unknown": "offline", "Mixed": "warning"}.get(
                        state_text, "info")
        return StatusPill(text=state_text, severity=severity)

    def _render_connected(self):
        _clear_layout(self._body)
        self._stale_label.setText("")
        snap = self._snapshot or {}
        rooms = snap.get("rooms") or []
        unassigned_devices = snap.get("unassigned_devices") or []
        unassigned_states = snap.get("unassigned_states") or []
        if not rooms and not unassigned_devices and not unassigned_states:
            from .design_system.empty import EmptyState
            empty = EmptyState(
                sentence="This hub has no rooms or devices yet.",
                suggested_task="Add a device to the hub, then "
                               "refresh.",
                action_label="Refresh")
            empty.action_requested.connect(self.refresh)
            self._body.addWidget(empty)
            return
        for room in rooms:
            title = _row_title(room.get("name") or "Unnamed room")
            title.setObjectName("sectionTitle")
            self._body.addWidget(title)
            for device in room.get("devices") or []:
                self._body.addWidget(
                    self._device_row(device, room.get("name") or ""))
            if not room.get("devices"):
                self._body.addWidget(_row_sub(
                    "No devices in this room."))
        if unassigned_devices:
            title = _row_title("No room assigned")
            title.setObjectName("sectionTitle")
            self._body.addWidget(title)
            for device in unassigned_devices:
                self._body.addWidget(self._device_row(device, ""))
        if unassigned_states:
            title = _row_title("Entities without a device")
            title.setObjectName("sectionTitle")
            self._body.addWidget(title)
            for view in unassigned_states:
                card, lay = _card_row(self._body)
                head = QHBoxLayout()
                head.addWidget(_row_title(view.get("entity_id") or ""))
                head.addStretch()
                head.addWidget(self._pill_for(view.get("state") or ""))
                lay.addLayout(head)
                lay.addWidget(_row_sub(
                    f"{view.get('name') or ''} · Last seen "
                    f"{view.get('last_seen') or '—'}"))
                btns = QHBoxLayout()
                details = _chip("Details")
                details.clicked.connect(
                    lambda _c=False, v=view:
                        self._open_entity_detail(v))
                btns.addWidget(details)
                btns.addStretch()
                lay.addLayout(btns)

    def _device_row(self, device, room_name):
        card, lay = _card_row(self._body)
        head = QHBoxLayout()
        head.addWidget(_row_title(device.get("name") or "Unnamed device"))
        head.addStretch()
        head.addWidget(self._pill_for(device.get("state_text") or "Unknown"))
        lay.addLayout(head)
        linked = device.get("states") or []
        if linked:
            names = ", ".join(
                f"{v.get('name') or v.get('entity_id')}: "
                f"{v.get('state')}" for v in linked)
            lay.addWidget(_row_sub(names))
        else:
            lay.addWidget(_row_sub("Unknown — the hub reports no "
                                   "state for this device."))
        btns = QHBoxLayout()
        details = _chip("Details")
        details.clicked.connect(
            lambda _c=False, d=device, r=room_name:
                self._open_device_detail(d, r))
        btns.addWidget(details)
        btns.addStretch()
        lay.addLayout(btns)
        return card

    # ------------------------------------------------------------ drawer
    def _drawer_body(self, title):
        layout = self._drawer.content_layout()
        _clear_layout(layout)
        self._drawer._title.setText(title)
        return layout

    def _controllable(self, entity_id):
        """Both gate conditions, from the stored config: on the
        per-room allowlist AND Assist-exposed. The page NEVER
        offers a control outside the allowlist (P5-11)."""
        cfg = self._config or {}
        return (entity_id in (cfg.get("allowlist") or {})
                and entity_id in (cfg.get("assist_exposed") or set()))

    def _open_device_detail(self, device, room_name):
        if _is_private():
            return
        layout = self._drawer_body(device.get("name") or "Device")
        layout.addWidget(_row_sub(
            f"Room: {room_name or 'No room assigned'}"))
        linked = device.get("states") or []
        if not linked:
            layout.addWidget(_row_sub(
                "Unknown — the hub reports no state for this device."))
        for view in linked:
            self._entity_block(layout, view, room_name)
        close = _chip("Close")
        close.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        layout.addWidget(close)
        _open_drawer(self._win, self._drawer, self._main)

    def _open_entity_detail(self, view):
        if _is_private():
            return
        layout = self._drawer_body(view.get("entity_id") or "Entity")
        self._entity_block(layout, view, "")
        close = _chip("Close")
        close.clicked.connect(
            lambda: _close_drawer(self._win, self._drawer, self._main))
        layout.addWidget(close)
        _open_drawer(self._win, self._drawer, self._main)

    def _entity_block(self, layout, view, room_name):
        entity_id = view.get("entity_id") or ""
        name = view.get("name") or entity_id
        title = _row_title(name)
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        layout.addWidget(_row_sub(
            f"{entity_id} · State: {view.get('state') or 'unknown'} · "
            f"Last seen {view.get('last_seen') or '—'}"))
        if not self._controllable(entity_id):
            layout.addWidget(_row_sub(
                "Not allowed to be controlled — it isn't on the "
                "allowlist in Settings, or isn't exposed to Assist."))
            return
        status = QLabel("")
        status.setObjectName("muted")
        status.setWordWrap(True)
        layout.addWidget(status)
        for action, verb in (("turn_on", "on"), ("turn_off", "off"),
                             ("toggle", "toggled")):
            btn = _chip(f"Turn {verb}" if action != "toggle"
                        else "Toggle")
            btn.clicked.connect(
                lambda _c=False, a=action, v=verb, e=entity_id,
                n=name, r=room_name, s=status:
                    self._on_control(a, v, e, n, r, s))
            layout.addWidget(btn)

    def _on_control(self, action, verb, entity_id, name, room_name,
                    status):
        """One control, confirm-gated through the inline approval
        card (exact target + intended change), then the state is
        re-read and the receipt names what the hub confirmed. A
        failed toggle leaves the state as last read and says so —
        the intended state is never shown as current."""
        domain = entity_id.split(".", 1)[0] if "." in entity_id else ""
        label = f"{name} ({room_name})" if room_name else name
        sentence = (f"Turn {name} {verb}." if action != "toggle"
                    else f"Toggle {name}.")
        allowed = _call_win(
            self._win, "_views_confirm_write", "home_devices", label,
            sentence, default=False)
        if not allowed:
            return
        client, reason = self._build_client()
        if client is None:
            status.setText(reason or "The hub couldn't be reached.")
            return
        try:
            before = client.get_state(entity_id)
            old_state = str((before or {}).get("state") or "unknown")
        except Exception:  # noqa: BLE001 — the old state stays unknown
            old_state = "unknown"
        try:
            client.call_service(domain, action, entity_id)
        except Exception as err:  # noqa: BLE001 — refused or failed
            status.setText(
                f"Still {old_state} — the hub didn't confirm the "
                f"change. {self._error_line(err)}")
            return
        try:
            after = client.get_state(entity_id)
            new_state = str((after or {}).get("state") or "unknown")
        except Exception:  # noqa: BLE001 — the call landed, the
            # confirm read failed: say exactly that.
            status.setText(
                "Sent — but the hub didn't confirm the new state. "
                "Refresh to re-read it.")
            self.refresh()
            return
        status.setText(f"{name} is {new_state} — confirmed by the hub.")
        _call_win(self._win, "_views_toast",
                  f"{name} is {new_state} — confirmed by the hub.")
        self.refresh()


# --------------------------------------------------------------------- mixin

class PagesP5Mixin:
    """Phase 5 page builders (Notes / Finance / Devices) + their
    on-show refreshes. The tall pages are wrapped in the outer
    QScrollArea the way the Doctor page is (Pulse/Doctor pattern):
    content keeps its natural heights and the page scrolls instead
    of squashing."""

    def _build_notes_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Notes", "Your notes, on this machine. Nothing here "
                     "leaves it.", kicker=True)
        self.notes_page = None
        try:
            self.notes_page = NotesPage(self)
            layout.addWidget(self.notes_page, stretch=1)
        except Exception as e:  # noqa: BLE001 — the page must build
            print(f"[VYUK] Notes page unavailable: {e}")
            unavailable = QLabel("Notes is unavailable on this machine.")
            unavailable.setObjectName("muted")
            layout.addWidget(unavailable)
        return self._p5_wrap(page)

    def _build_finance_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Finance", "Money you record yourself. No bank "
                       "connection — VYUK cannot see your accounts.",
            kicker=True)
        self.finance_page = None
        try:
            self.finance_page = FinancePage(self)
            layout.addWidget(self.finance_page, stretch=1)
        except Exception as e:  # noqa: BLE001 — the page must build
            print(f"[VYUK] Finance page unavailable: {e}")
            unavailable = QLabel(
                "Finance is unavailable on this machine.")
            unavailable.setObjectName("muted")
            layout.addWidget(unavailable)
        return self._p5_wrap(page)

    def _build_devices_page(self) -> QWidget:
        # The subtitle is the scope line, true in every state —
        # the body carries the state (not-configured / connected
        # / no-permission). It must never claim "nothing
        # connected" while rooms are listed below it.
        page, layout = self._page_shell(
            "Devices", "Your home hub, on your network.",
            kicker=True)
        self.devices_page = None
        try:
            self.devices_page = DevicesPage(self)
            layout.addWidget(self.devices_page, stretch=1)
        except Exception as e:  # noqa: BLE001 — the page must build
            print(f"[VYUK] Devices page unavailable: {e}")
            unavailable = QLabel(
                "Devices is unavailable on this machine.")
            unavailable.setObjectName("muted")
            layout.addWidget(unavailable)
        return self._p5_wrap(page)

    def _p5_wrap(self, page) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        page.setAutoFillBackground(False)
        try:
            self._apply_theme_to_painted_pages()
        except Exception:  # noqa: BLE001 — the page must still build
            pass
        return scroll

    def _refresh_notes_page(self) -> None:
        page = getattr(self, "notes_page", None)
        if page is not None:
            page.refresh()

    def _refresh_finance_page(self) -> None:
        page = getattr(self, "finance_page", None)
        if page is not None:
            page.refresh()

    def _refresh_devices_page(self) -> None:
        page = getattr(self, "devices_page", None)
        if page is not None:
            page.refresh()
