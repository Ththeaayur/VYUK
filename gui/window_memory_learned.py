"""Learned facts + keys/reminders/home/plugins/autostart.

Split from gui/window_memory.py (GUI split #12 batch 1, move only).
"""

import datetime
from PySide6.QtCore import (
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QKeyEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import discover_plugins
from voice_assistant.core.gui_facade import scheduler
from .empty_art import GenerativeArt
from .memory_view import reminders_view
from .room_activity import scheduled_line
from .task_state import (
    instrument_row,
    instrument_row_title,
    list_item_size_hint,
)
from .window_chat import ChatMixin  # v5.50 item 43: _delete_layout_items lives there now
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set


class MemoryLearnedMixin:
    # --------------------------------------- she learns you (v2.6)
    def _build_learned_section(self, layout):
        """The 'Learned about you' card on the Memory page: master
        toggle, the learned-fact rows (rebuilt on page show, like the
        routines list), and the forget-everything two-tap button."""
        card = QFrame()
        card.setObjectName("card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        title = QLabel("Learned about you")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        self.chk_learn = QCheckBox("Learn from our conversations")
        header.addWidget(self.chk_learn)
        c_layout.addLayout(header)
        self.learned_layout = QVBoxLayout()
        self.learned_layout.setSpacing(6)
        c_layout.addLayout(self.learned_layout)
        foot = QHBoxLayout()
        foot.addStretch()
        self.btn_forget_all_learned = QPushButton("Forget everything learned")
        foot.addWidget(self.btn_forget_all_learned)
        c_layout.addLayout(foot)
        self.learned_card = card
        layout.addWidget(card)
        # States-then-connections, the presence-card discipline:
        # setting the saved state must not fire the handler.
        self.chk_learn.setChecked(
            bool(qsettings_get("learning/enabled", settings=self.settings)))
        self.chk_learn.toggled.connect(self._on_learning_toggled)
        self.btn_forget_all_learned.clicked.connect(self._forget_all_learned)
        self._refresh_learned_facts()

    def _learned_seen_ids(self) -> set:
        """Ids the user has already seen on the Memory page — stored
        as strings so int/str id shapes compare equal."""
        try:
            raw = qsettings_get("learning/seen_ids", settings=self.settings) or []
        except Exception:  # noqa: BLE001
            return set()
        if isinstance(raw, (str, int)):
            raw = [raw]
        return {str(v) for v in raw}

    def _store_learned_seen_ids(self, ids):
        try:
            qsettings_set("learning/seen_ids", [str(i) for i in ids if i is not None], settings=self.settings)
        except Exception:  # noqa: BLE001
            pass

    def _learned_fact_count(self) -> int:
        """Rows the Learned card shows right now (E-F204): the
        header counts them alongside the list rows. Guarded like
        the card refresh itself — no module, or a failing read,
        counts as zero — and it counts exactly what the card
        renders (one row per dict fact), so the count cannot drift
        from the rows."""
        module = self._learning_module()
        if module is None:
            return 0
        try:
            facts = module.get_learned_facts() or []
        except Exception:  # noqa: BLE001 — a broken read is zero rows
            return 0
        return sum(1 for f in facts if isinstance(f, dict))

    def _refresh_learned_facts(self, mark_seen: bool = False):
        """Rebuild the learned-fact rows. Facts not yet in
        learning/seen_ids render with a '• New' suffix; with
        mark_seen=True (the page is being shown, or a forget just
        happened in front of the user) the current id set becomes
        the seen set AFTER rendering."""
        layout = getattr(self, "learned_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Hide NOW, not just deleteLater: until the deferred
                # delete lands, an orphaned widget would keep painting
                # at its old geometry, over the rows that replace it.
                widget.hide()
                widget.deleteLater()
            elif item.layout() is not None:
                ChatMixin._delete_layout_items(item.layout())  # v5.50 item 43: was MainWindow._delete_layout_items (same staticmethod, now on ChatMixin)
        module = self._learning_module()
        for attr in ("chk_learn", "btn_forget_all_learned"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setEnabled(module is not None)
        if module is None:
            note = QLabel("Learning isn't available in this build.")
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)
            return
        try:
            facts = list(module.get_learned_facts() or [])
        except Exception:  # noqa: BLE001
            facts = []
        if not facts:
            # v5.24 (Eyes E34): the learned card's empty state
            # gets the generative art too (its own seed).
            layout.addWidget(GenerativeArt(seed=23))
            empty = QLabel(
                "Nothing learned yet — as we talk, VYUK quietly picks up "
                "things like your name and what you like, and lists them "
                "here. Nothing is kept anywhere else.")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            layout.addWidget(empty)
            if mark_seen:
                self._store_learned_seen_ids([])
            return
        seen = self._learned_seen_ids()
        for fact in facts:
            if isinstance(fact, dict):
                layout.addWidget(self._learned_fact_row(
                    fact, is_new=str(fact.get("id")) not in seen))
        if mark_seen:
            self._store_learned_seen_ids(
                [f.get("id") for f in facts if isinstance(f, dict)])

    def _learned_fact_row(self, fact: dict, is_new: bool) -> QWidget:
        display = str(fact.get("display", "") or "").strip()
        fact_id = fact.get("id")
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        lbl = QLabel(display)
        lbl.setWordWrap(True)
        top.addWidget(lbl, 1)
        if is_new:
            new_lbl = QLabel("• New")
            new_lbl.setObjectName("muted")
            top.addWidget(new_lbl)
        btn_forget = QPushButton("Forget")
        btn_forget.setObjectName("chipBtn")
        btn_forget.clicked.connect(
            lambda _=False, fid=fact_id, d=display:
            self._forget_learned_fact(fid, d))
        top.addWidget(btn_forget)
        v.addLayout(top)
        # E-F203: no raw `key: value` sub-line. The display sentence
        # IS the fact — core.memory.learning.display_for derives it
        # from the stored pair and always carries the value verbatim
        # (every branch embeds it; the default branch IS
        # "key: value"), so the pair can only restate the display.
        # The old guard could not tell a restatement from new
        # information: the ": " separator breaks the substring
        # test, template keys are machine slugs
        # (likes_black_coffee_in), and values are paraphrased in
        # the display ("Mon/Wed/Fri" vs "Monday, Wednesday and
        # Friday") — no string comparison judges that. The raw key
        # adds nothing a user can read, so the line goes instead
        # of being guarded.
        return wrap

    def _on_learning_toggled(self, enabled: bool):
        # Off means off — _learn_from_utterance checks this key BEFORE
        # calling the learner, so nothing is learned in the background.
        qsettings_set("learning/enabled", bool(enabled), settings=self.settings)

    def _forget_learned_fact(self, fact_id, display: str):
        module = self._learning_module()
        ok = False
        if module is not None:
            try:
                ok = bool(module.forget_learned_fact(fact_id))
            except Exception:  # noqa: BLE001
                ok = False
        self._refresh_learned_facts(mark_seen=True)
        # E-F204: the header counts the card rows (and the list
        # still shows the forgotten fact until it is re-read), so
        # forgetting from the card re-reads the list + header too.
        self._refresh_memories()
        if ok:
            self._add_system(
                f"Forgot — I'll no longer remember {display}.")

    def _forget_all_learned(self):
        """Two-tap bulk forget (the clear-history pattern): the first
        tap arms the button, a second within ~4 s erases everything
        conversation taught her. General hand-saved facts are NOT
        touched — that list is the user's own."""
        if not getattr(self, "_learned_clear_armed", False):
            self._learned_clear_armed = True
            if hasattr(self, "btn_forget_all_learned"):
                self.btn_forget_all_learned.setText(
                    "Tap again to forget everything")
            QTimer.singleShot(4000, self._disarm_forget_all_learned)
            return
        self._learned_clear_armed = False
        if hasattr(self, "btn_forget_all_learned"):
            self.btn_forget_all_learned.setText("Forget everything learned")
        module = self._learning_module()
        count = 0
        if module is not None:
            try:
                count = int(module.forget_all_learned() or 0)
            except Exception:  # noqa: BLE001
                count = 0
        self._refresh_learned_facts(mark_seen=True)
        # E-F204: as above — the header (and list) follow the card.
        self._refresh_memories()
        if count:
            self._add_system(
                "Forgot everything VYUK learned from our conversations.")
        else:
            self._add_system("There was nothing learned to forget.")

    def _disarm_forget_all_learned(self):
        if not getattr(self, "_learned_clear_armed", False):
            return
        self._learned_clear_armed = False
        if hasattr(self, "btn_forget_all_learned"):
            try:
                self.btn_forget_all_learned.setText("Forget everything learned")
            except RuntimeError:
                pass

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            focused = self.focusWidget()
            if not isinstance(focused, (QLineEdit,)):
                self._on_ptt_pressed()
                event.accept()
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
            focused = self.focusWidget()
            if not isinstance(focused, (QLineEdit,)):
                self._on_ptt_released()
                event.accept()
                return
        super().keyReleaseEvent(event)

    def _add_reminder_from_ui(self):
        msg = self.input_reminder_msg.text().strip()
        if not msg:
            self._notify("Type the reminder first — e.g. “Call the lab”.")
            return
        now = datetime.datetime.now()
        at_mode = (getattr(self, "chk_reminder_at", None) is not None
                   and self.chk_reminder_at.isChecked())
        if at_mode:
            fire_at = now.replace(
                hour=self.input_reminder_h.value(),
                minute=self.input_reminder_m.value(),
                second=0, microsecond=0)
            tomorrow = fire_at <= now
            if tomorrow:
                fire_at += datetime.timedelta(days=1)
        else:
            minutes = self.input_reminder_min.value()
            fire_at = now + datetime.timedelta(minutes=minutes)
            tomorrow = False
        label = f"ui_reminder_{now.strftime('%H%M%S%f')}"
        scheduler.add_reminder(label=label, fire_at=fire_at,
                               message=msg, repeat=False)
        self.input_reminder_msg.clear()
        self._refresh_reminders()
        if at_mode:
            when = fire_at.strftime("%H:%M")
            self._notify(
                f"Reminder set — I'll tell you "
                f"{'tomorrow ' if tomorrow else 'today '}at {when}.")
        else:
            self._notify(
                f"Reminder set — I'll tell you in {minutes} min.")

    def _enable_briefing(self):
        # GAPS-26 (S-V4): one briefing implementation — the QTimer
        # _check_briefing path (gui/window_memory_routines.py),
        # driven by the presence keys. This button used to create
        # a second, scheduler-side briefing (a fixed-string
        # reminder via the retired scheduler-side briefing
        # method); it now just enables the single path at the
        # hour picked here. Any stray
        # scheduler-side briefing reminder left from before the
        # merge is still cancelled, and the startup migration
        # (migrate_legacy_briefing_records) preserves its time.
        hour = self.spin_brief_hour.value()
        scheduler.cancel_reminder("morning_briefing")
        qsettings_set("presence/briefing_enabled", True, settings=self.settings)
        qsettings_set("presence/briefing_time", f"{hour:02d}:00",
                      settings=self.settings)
        self._refresh_reminders()
        self._notify(f"Morning briefing on — every day at {hour:02d}:00.")

    def _refresh_home(self):
        # GAPS-28 (G-V1): the reminder read and the "next" choice
        # live in gui/memory_view.py — this method only renders.
        # Sci-fi Home: the home strip is gone — the next reminder
        # reads in the briefing schedule, so this method only
        # refreshes the briefing card (which renders the same
        # reminder from the same source).
        # v5.57 (redo): reminder changes refresh the briefing card too.
        try:
            self._refresh_briefing_card()
        except Exception:  # noqa: BLE001
            pass

    def _refresh_reminders(self):
        if not hasattr(self, "reminders_list"):
            return
        self.reminders_list.clear()
        # GAPS-28 (G-V1): the reminder read and the summary string
        # live in gui/memory_view.py — this method only renders.
        view = reminders_view()
        reminders = view["reminders"]
        if not reminders:
            self.reminders_list.addItem("No reminders yet.")
        for r in reminders:
            # Screen 3 Activity: the row is an instrument — the
            # label (with the repeat on it), the message as the
            # one reading, the fire time right-aligned in the mono
            # face. The item carries NO text: QListWidget paints
            # an item's text behind its widget, so the old
            # "message · fire_at · status" string would
            # double-paint over the row. The status + repeat live
            # on the widget (label + fired dimming); the label
            # string rides on UserRole for Remove selected.
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r["label"])
            self.reminders_list.addItem(item)
            widget = self._reminder_row_widget(r)
            item.setSizeHint(list_item_size_hint(widget))
            self.reminders_list.setItemWidget(item, widget)
        # UI rethink Build 2: the Scheduled headline line, from
        # the same reminder read — no second read. The old
        # summary label is gone: its line IS the headline now
        # ("2 due today · next 09:00 · 1 already spoken").
        self._update_activity_headline(
            "scheduled",
            scheduled_line(reminders, datetime.datetime.now().date()))
        self._refresh_home()
        self._refresh_routines()
        # Build 2: the Scheduled page's content changed, so the
        # pane re-hugs the current tab.
        try:
            self._fit_activity_tabs_height()
        except Exception:  # noqa: BLE001 — the fit is cosmetic
            pass

    def _reminder_row_widget(self, reminder):
        """One reminder row widget (Screen 3 Activity)."""
        wrap = instrument_row()
        wrap.setObjectName("activityRow")
        if reminder.get("fired"):
            wrap.setProperty("fired", "true")
        row = QHBoxLayout(wrap)
        # No vertical margins of its own: the QListWidget::item
        # padding is the row's breathing room (see
        # list_item_size_hint).
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        label = QLabel(
            "REMINDER · DAILY" if reminder.get("repeat") else "REMINDER")
        label.setObjectName("activityRowLabel")
        row.addWidget(label)
        message = str(reminder.get("message", ""))
        value = QLabel(message)
        value.setObjectName("activityRowValue")
        value.setWordWrap(False)
        row.addWidget(value, stretch=1)
        instrument_row_title(wrap, value, message)
        number = QLabel(str(reminder.get("fire_at", "")))
        number.setObjectName("activityRowNumber")
        number.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(number)
        return wrap

    def _reload_plugins(self):
        self._plugins = discover_plugins()
        if not hasattr(self, "plugin_list"):
            return
        self.plugin_list.clear()
        if not self._plugins:
            self.plugin_list.addItem("No extra skills yet. Skills a developer gives you go in the plugins folder.")
        else:
            for p in self._plugins:
                name = p.get("name", "?").replace("_", " ")
                desc = p.get("description", "")
                self.plugin_list.addItem(f"{name}  —  {desc}")

    # ------------------------------------------------- Presence (v2.1)
    @staticmethod
    def _autostart_enabled():
        """core.presence.autostart_enabled(), or None when unavailable
        (non-Windows, or the presence module is missing)."""
        try:
            from voice_assistant.core.gui_facade import presence

            return presence.autostart_enabled()
        except Exception:  # noqa: BLE001
            return None
