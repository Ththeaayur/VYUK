"""The Always-allow screen (UI rethink Build 4, plan §4/§6.4).

Settings → Privacy & permissions → "Always allow": the single
home for every remembered grant. One row per grant — thing
13 Sora semibold, terms one line 13 Sora muted, granted-when
11.5 mono, Remove per row — with "Forget all remembered
kinds" (confirm-kinds only, D-R24) at the bottom.

The screen's first line is "Some things will always ask."
(§4.9), with the never-list sentence verbatim at the top.
The full T&Cs (§4.6) show the first time the owner opens
the screen (QSettings flag), and again behind "Read the
full terms". Opening the screen runs the §4.6 migration
(drop_stale_allowlist_entries): a stored kind that no
longer passes the safe-shape test is dropped and the drop
noted on the status line.

Opened from the Remembered approvals card (the entry Build 3
built). Double-open guard on the owner, same shape as the
browser-connect dialog. Closing refreshes the card's list.

The middle (terms + rows) lives in a scroll area: with the
full terms AND rows the content needs ~1000 px tall, and a
dialog shown without an explicit size is clamped to the
screen — on a short screen (768 px laptop, or the lab's
800 px offscreen) the word-wrapped terms were squeezed to
one-line heights and the paragraphs overlapped into a
garbled block (caught by the Build 4 renders). The scroll
area takes the spare height instead, so the terms stay
readable at any screen height; the header, the terms
toggle, the status line and the bottom row stay fixed.
The dialog opens at its full sizeHint height on a tall
screen, and at the screen height less a margin on a short
one (never below 480 px tall).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import always_allow as _aa
from .design_system.dialogs import HudDialog
from .style import TEXT_MUTED, MONO_FONT_STACK

_TERMS_SEEN_KEY = "always_allow/terms_seen"


def _terms_seen(settings) -> bool:
    try:
        if settings is None:
            return False
        return bool(settings.value(_TERMS_SEEN_KEY, False, type=bool))
    except Exception:  # noqa: BLE001 — unseen, show the terms
        return False


def _mark_terms_seen(settings) -> None:
    try:
        if settings is not None:
            settings.setValue(_TERMS_SEEN_KEY, True)
    except Exception:  # noqa: BLE001 — the terms show again
        pass


class AlwaysAllowDialog(HudDialog):
    """The Always-allow screen as a modal dialog."""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent, title="Always allow")
        self.setAccessibleName("Always allow dialog")
        self.setMinimumWidth(600)
        self._settings = settings
        if self._settings is None:
            self._settings = getattr(parent, "settings", None)
        layout = self.content_layout()

        first = QLabel(_aa.SCREEN_FIRST_LINE)
        first.setStyleSheet("font-size: 13px; font-weight: 600;")
        first.setWordWrap(True)
        layout.addWidget(first)
        never = QLabel(_aa.NEVER_LIST_SENTENCE)
        never.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px;")
        never.setWordWrap(True)
        layout.addWidget(never)

        # The full T&Cs (§4.6): shown in full the first time
        # the screen opens; afterwards behind "Read the full
        # terms" so the screen stays a list of rows, not a
        # wall of text. The toggle sits ABOVE the scroll area
        # so it never scrolls away from the terms it toggles.
        self.btn_terms = QPushButton("Read the full terms")
        self.btn_terms.setObjectName("chipBtn")
        self.btn_terms.setAccessibleName("Read the full terms")
        self.btn_terms.setCheckable(True)
        show_terms = not _terms_seen(self._settings)
        self.btn_terms.setChecked(show_terms)
        self.btn_terms.toggled.connect(self._terms_widget_set_visible)
        layout.addWidget(self.btn_terms)

        # The scrollable middle: terms + rows + empty state.
        # (See the module docstring: without this, a short
        # screen squeezes the word-wrapped terms into
        # overlapping lines.)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 8, 0, 0)
        inner_layout.setSpacing(8)
        self._terms_widget = QWidget()
        terms_layout = QVBoxLayout(self._terms_widget)
        terms_layout.setContentsMargins(0, 0, 0, 0)
        terms_layout.setSpacing(6)
        terms_title = QLabel(_aa.TERMS_TITLE)
        terms_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        terms_title.setWordWrap(True)
        terms_layout.addWidget(terms_title)
        for para in _aa.TERMS_PARAGRAPHS:
            label = QLabel(para)
            label.setStyleSheet("font-size: 13px;")
            label.setWordWrap(True)
            terms_layout.addWidget(label)
        self._terms_widget.setVisible(show_terms)
        if show_terms:
            _mark_terms_seen(self._settings)
        inner_layout.addWidget(self._terms_widget)

        self._rows_container = QVBoxLayout()
        self._rows_container.setContentsMargins(0, 8, 0, 0)
        self._rows_container.setSpacing(8)
        inner_layout.addLayout(self._rows_container)
        self._empty_label = QLabel(
            "Nothing remembered — VYUK asks before every action "
            "that needs it.")
        self._empty_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 13px;")
        self._empty_label.setWordWrap(True)
        inner_layout.addWidget(self._empty_label)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        # QScrollArea.setWidget forces autoFillBackground True
        # on its widget — and the viewport follows — so the
        # body/viewport would paint their palette Window
        # (light) and wash out the dialog's light text.
        # Reset both AFTER setWidget, so the dialog's dark
        # surface shows through (the firstopen_prompt
        # pattern, same cause).
        inner.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        self._scroll = scroll
        layout.addWidget(scroll, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("cardHint")
        self.status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 13px;")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 8, 0, 0)
        self.btn_forget_all = QPushButton("Forget all remembered kinds")
        self.btn_forget_all.setObjectName("chipBtn")
        self.btn_forget_all.setAccessibleName(
            "Forget all remembered kinds")
        self.btn_forget_all.clicked.connect(self._on_forget_all)
        bottom.addWidget(self.btn_forget_all)
        bottom.addStretch(1)
        btn_done = QPushButton("Done")
        btn_done.setObjectName("chipBtn")
        btn_done.setAccessibleName("Close Always allow dialog")
        btn_done.clicked.connect(self.close)
        bottom.addWidget(btn_done)
        layout.addLayout(bottom)

        # §4.6 migration on open: drop any stored kind that no
        # longer passes the safe-shape test, and say so.
        try:
            dropped = _aa.drop_stale_allowlist_entries()
        except Exception:  # noqa: BLE001 — the screen still opens
            dropped = []
        self._refresh_rows()
        if dropped:
            self._set_status(
                f"Switched off {len(dropped)} remembered kind"
                f"{'s' if len(dropped) != 1 else ''} whose terms "
                "changed — VYUK will ask about "
                f"{'them' if len(dropped) != 1 else 'it'} again.")

        # Open tall enough to read: full sizeHint height on a
        # tall screen, screen height less a margin on a short
        # one (the scroll area takes the rest), never below
        # 480 px — a dialog shown without an explicit size is
        # clamped to the screen and squeezes its content.
        try:
            screen = QApplication.primaryScreen()
            available = (screen.availableGeometry().height()
                         if screen is not None else 0)
        except Exception:  # noqa: BLE001 — fall back to sizeHint
            available = 0
        want = self.sizeHint().height()
        if available:
            height = max(480, min(want, available - 40))
        else:
            height = max(480, want)
        self.resize(max(600, self.sizeHint().width()), height)

    def _terms_widget_set_visible(self, visible: bool) -> None:
        """The "Read the full terms" toggle. A named slot
        (not a direct setVisible connect) because the button
        is built before the terms widget it toggles."""
        try:
            self._terms_widget.setVisible(bool(visible))
        except Exception:  # noqa: BLE001 — the toggle is courtesy
            pass

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def _clear_rows(self) -> None:
        while self._rows_container.count():
            item = self._rows_container.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hide NOW, delete on the next loop turn:
                # a row taken out of the layout keeps its old
                # geometry and would paint as a ghost until
                # the deferred delete lands. Hiding is
                # synchronous and safe mid-slot (the Remove
                # button lives inside the row being cleared).
                widget.hide()
                widget.deleteLater()

    def _refresh_rows(self) -> None:
        self._clear_rows()
        try:
            rows = _aa.list_grants()
        except Exception:  # noqa: BLE001 — empty state, not a crash
            rows = []
        has_kinds = False
        for row in rows:
            if row.get("layer") == "kind":
                has_kinds = True
            self._rows_container.addWidget(
                self._build_row(row))
        self._empty_label.setVisible(not rows)
        # Forget-all covers remembered kinds ONLY (§4.4):
        # it shows only while a kind row exists.
        self.btn_forget_all.setVisible(has_kinds)

    def _build_row(self, row: dict) -> QFrame:
        subject = str(row.get("subject", ""))
        thing = str(row.get("thing", subject))
        frame = QFrame()
        frame.setObjectName(f"alwaysAllowRow:{subject}")
        frame.setAccessibleName(f"Always allow row: {thing}")
        outer = QHBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        thing_label = QLabel(thing)
        thing_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        thing_label.setWordWrap(True)
        left.addWidget(thing_label)
        terms_label = QLabel(str(row.get("terms", "")))
        terms_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 13px;")
        terms_label.setWordWrap(True)
        left.addWidget(terms_label)
        when_label = QLabel(str(row.get("granted_when", "")))
        when_label.setStyleSheet(
            f"font-family: {MONO_FONT_STACK}; "
            f"color: {TEXT_MUTED}; font-size: 11.5px;")
        left.addWidget(when_label)
        outer.addLayout(left, 1)
        btn_remove = QPushButton("Remove")
        btn_remove.setObjectName("chipBtn")
        btn_remove.setAccessibleName(f"Remove {thing}")
        btn_remove.clicked.connect(
            lambda _checked=False, s=subject, t=thing:
            self._on_remove(s, t))
        outer.addWidget(btn_remove)
        return frame

    def _on_remove(self, subject: str, thing: str) -> None:
        try:
            ok = _aa.revoke_subject(subject)
        except Exception:  # noqa: BLE001 — the row stays, status says so
            ok = False
        if ok:
            self._set_status(
                f"Removed {thing} — VYUK will ask about it again "
                "next time.")
        else:
            self._set_status(
                f"Couldn't remove {thing} — try again, or remove it "
                "in Settings → Permissions.")
        self._refresh_rows()

    def _on_forget_all(self) -> None:
        try:
            forgotten = _aa.forget_all_kinds()
        except Exception:  # noqa: BLE001 — nothing forgotten
            forgotten = []
        if forgotten:
            self._set_status(
                f"Forgot {len(forgotten)} remembered kind"
                f"{'s' if len(forgotten) != 1 else ''} — VYUK will "
                "ask about them again.")
        else:
            self._set_status("Nothing remembered to forget.")
        self._refresh_rows()


def open_always_allow_dialog(owner) -> None:
    """Open the Always-allow screen for ``owner`` (the main
    window), or raise the open one. The double-open guard
    lives on the owner as ``_always_allow_dialog``. Closing
    refreshes the Remembered approvals card's list, so the
    card and the screen can never disagree. Never raises."""
    dlg = getattr(owner, "_always_allow_dialog", None)
    if dlg is not None:
        try:
            dlg.raise_()
            dlg.activateWindow()
            return
        except Exception:  # noqa: BLE001 — rebuild below
            pass
    try:
        dlg = AlwaysAllowDialog(owner)

        def _on_finished(*_args):
            try:
                owner._always_allow_dialog = None
            except Exception:  # noqa: BLE001
                pass
            try:
                refresh = getattr(owner, "_refresh_allowlist", None)
                if callable(refresh):
                    refresh()
            except Exception:  # noqa: BLE001 — the card refreshes
                pass  # on its own next open

        dlg.finished.connect(_on_finished)
        owner._always_allow_dialog = dlg
        dlg.show()
    except Exception:  # noqa: BLE001 — the card stays the way back
        pass
