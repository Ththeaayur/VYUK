"""Learned facts + keys/reminders/home/plugins/autostart.

Split from gui/window_memory.py (GUI split #12 batch 1, move only).
"""

import datetime
from PySide6.QtCore import (
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QFontMetrics,
    QKeyEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from .design_system.empty import EmptyState
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


class _ScheduledTimelineRow(QWidget):
    """One Scheduled-timeline row (Phase 1, §3).

    A row (bg fill + 1 px LINE_SUBTLE edge, no vertical margins
    of its own) whose reading re-elides on resize, like the
    task-state rows. ``is_past`` is the dimming truth the legs
    read: True for an event whose fire time is before now (or
    that already fired), False otherwise — "No time set" rows
    are never past. A click selects the row (reminders only:
    the selection drives Remove selected).
    """

    def __init__(self, parent=None, *, on_select=None):
        super().__init__(parent)
        self.setObjectName("scheduledTimelineRow")
        self.is_past = False
        self._on_select = on_select
        self._title_lbl = None
        self._title_full = ""
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        if callable(self._on_select):
            self._on_select()
        super().mousePressEvent(event)

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        label = self._title_lbl
        if label is None or label.width() <= 0:
            return
        elided = QFontMetrics(label.font()).elidedText(
            self._title_full, Qt.TextElideMode.ElideRight,
            label.width())
        if elided != label.text():
            label.setText(elided)


class MemoryLearnedMixin:
    # --------------------------------------- she learns you (v2.6)
    def _build_learned_section(self, layout):
        """The 'Learned about you' card on the Memory page: the
        learned-fact rows (rebuilt on page show, like the
        routines list), and the forget-everything two-tap button.
        Phase 1: the learning toggle MOVED to the Profile
        section — this card no longer owns it. The Profile
        section builds first, so chk_learn exists before the
        first _refresh_learned_facts() runs; the refresh guards
        it with getattr, so a missing toggle could never crash
        the card."""
        card = QFrame()
        card.setObjectName("card")
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        title = QLabel("Learned about you")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
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
        """Rows the Learned card shows right now. Guarded like
        the card refresh itself — no module, or a failing read,
        counts as zero — and it counts exactly what the card
        renders (one row per dict fact), so the count cannot drift
        from the rows. Hunt fix (c) item 6: the card shows all
        user facts (get_all_user_facts), not the "learned"
        category alone. Hunt fix (c) header round: this count no
        longer feeds the header — the header counts unique facts
        (memory_header_text), and these rows are the same facts
        already in the list, so adding them counted every fact
        twice. The method stays as the card's row-count accessor
        (the item-6 tests read it)."""
        module = self._learning_module()
        if module is None:
            return 0
        try:
            getter = getattr(module, "get_all_user_facts",
                             module.get_learned_facts)
            facts = getter() or []
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
                # setParent(None) FIRST (not just hide + deleteLater):
                # until the deferred delete lands, an orphaned widget
                # keeps answering findChildren AND keeps painting at
                # its old geometry, over the rows that replace it
                # (the orphan trap from legs 51/54).
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout() is not None:
                ChatMixin._delete_layout_items(item.layout())  # v5.50 item 43: was MainWindow._delete_layout_items (same staticmethod, now on ChatMixin)
        module = self._learning_module()
        for attr in ("chk_learn", "btn_forget_all_learned"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setEnabled(module is not None)
        if self._mem_private_on():
            # Private mode blanks the learned rows (M-01) but the
            # footer Forget-all stays enabled: forgetting stays
            # available while private mode is on.
            note = QLabel("Private mode is on")
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)
            return
        if module is None:
            note = QLabel("Learning isn't available in this build.")
            note.setObjectName("muted")
            note.setWordWrap(True)
            layout.addWidget(note)
            return
        # Hunt fix (c) item 6: the card shows all user facts
        # (general + preferences + inferred + learned), not the
        # "learned" category alone — that narrow read said
        # "Nothing learned yet" while the page header counted 5.
        try:
            getter = getattr(module, "get_all_user_facts",
                             module.get_learned_facts)
            facts = list(getter() or [])
        except Exception:  # noqa: BLE001
            facts = []
        if not facts:
            # v5.24 (Eyes E34): the learned card's empty state
            # gets the generative art too (its own seed) — except
            # on the Minimal profile, where decorative art is off
            # (the Knowledge Graph canvas is gated the same way in
            # _apply_hud_profile; this card's art is a separate
            # GenerativeArt, so it needs its own gate).
            profile = getattr(self, "_hud_profile_name", "") or str(
                qsettings_get("appearance/hud_profile") or "Cinematic")
            if profile != "Minimal":
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
                # Hunt fix (c) item 6: the card shows all user
                # facts now, so a row's Forget must work for
                # every row it shows (forget_user_fact); the
                # "learned"-only forget would refuse the
                # general / preferences / inferred rows.
                forgetter = getattr(module, "forget_user_fact",
                                    module.forget_learned_fact)
                ok = bool(forgetter(fact_id))
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
        # Phase 1 (§3): the visible surface is the timeline
        # (_rebuild_scheduled_timeline, via _refresh_routines
        # below). This list stays as its hidden selection backing
        # store — one item per reminder with the label on
        # UserRole, so _remove_selected_reminder keeps working
        # against the same identities; a timeline row's click
        # syncs its current item.
        for r in reminders:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, r["label"])
            self.reminders_list.addItem(item)
        _labels = {str(r.get("label")) for r in reminders}
        if getattr(self, "_timeline_selected_label", None) not in _labels:
            self._timeline_selected_label = None
        # UI rethink Build 2: the Scheduled headline line, from
        # the same reminder read — no second read. The old
        # summary label is gone: its line IS the headline now
        # ("2 due today · next 09:00 · 1 already spoken").
        self._update_activity_headline(
            "scheduled",
            scheduled_line(reminders, datetime.datetime.now().date()))
        self._refresh_home()
        self._refresh_routines()
        # Phase 1: Plans are a Scheduled-tab surface too — a plan
        # created after the page was built (voice, or another
        # surface) shows up on the next Activity refresh, not
        # only at page build.
        self._refresh_plans()
        self._update_reminder_remove_btn()
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

    def _select_timeline_reminder(self, label):
        """A timeline reminder row was clicked: it is the
        selection — Remove selected appears, and the hidden
        reminders_list mirrors it so _remove_selected_reminder
        cancels this exact reminder."""
        self._timeline_selected_label = str(label)
        lst = getattr(self, "reminders_list", None)
        if lst is not None:
            for i in range(lst.count()):
                item = lst.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == label:
                    lst.setCurrentItem(item)
                    break
        self._update_reminder_remove_btn()

    def _scheduled_timeline_row(self, event):
        """One timeline row widget from a timeline event dict."""
        from .style import LINE_SUBTLE, TEXT_MUTED
        from .task_state import instrument_row_title

        wrap = _ScheduledTimelineRow(
            on_select=event.get("on_select"))
        wrap.is_past = bool(event.get("is_past"))
        wrap.setStyleSheet(
            f"border: 1px solid {LINE_SUBTLE};")
        row = QHBoxLayout(wrap)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        label = QLabel(event["kind_label"])
        label.setObjectName("activityRowLabel")
        row.addWidget(label)
        title = str(event.get("title") or "")
        value = QLabel(title)
        value.setObjectName("activityRowValue")
        value.setWordWrap(False)
        row.addWidget(value, stretch=1)
        instrument_row_title(wrap, value, title)
        tag = event.get("tag")
        if tag:
            tag_lbl = QLabel(str(tag))
            tag_lbl.setObjectName("muted")
            row.addWidget(tag_lbl)
        number = QLabel(str(event.get("time_text") or ""))
        number.setObjectName("activityRowNumber")
        number.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(number)
        if wrap.is_past:
            # B-A05: past events dim to the muted text token,
            # never below the contrast floor; the dimming
            # applies to the row's text.
            for lbl in (label, value, number):
                lbl.setStyleSheet(f"color: {TEXT_MUTED};")
        return wrap

    def _rebuild_scheduled_timeline(self):
        """Rebuild the Scheduled timeline (Phase 1, §3/A-04/A-09).

        One chronological list — reminders + routines — ordered
        by the fire datetime (the scheduler record's fire_at
        datetime via get_all_reminders_with_epoch, and the
        routine's next run via planner.next_run, the same call
        _routine_next_text uses — NEVER the formatted display
        string). Day/time groups: Today, Tomorrow, This week,
        Later; a reminder with no parseable fire time renders
        under "No time set" — never hidden, never dimmed. Fired
        reminders render dimmed. Plans stay out of the timeline
        (they have no fire time) — they keep their own section
        below it. The spine + now-dot render only when the
        timeline has events; the dot's position and the past-dim
        state come from ``now`` at this refresh — no background
        timer, so a long-open tab's hinge goes stale until the
        next refresh (accepted and stated, B-A01).
        """
        layout = getattr(self, "_scheduled_timeline_layout", None)
        spine = getattr(self, "_scheduled_timeline_spine", None)
        if layout is None or spine is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # setParent(None) FIRST: deleteLater() alone leaves
                # the widget answering findChildren until the event
                # loop destroys it, so a second rebuild in the same
                # turn would double the rows (the orphan trap from
                # legs 51/54 — the Memory list had the same fix).
                widget.setParent(None)
                widget.deleteLater()
        now_local = datetime.datetime.now()
        now_epoch = datetime.datetime.now(
            datetime.timezone.utc).timestamp()
        today = now_local.date()
        events = []

        def _group(epoch):
            if epoch is None:
                return "No time set"
            day = datetime.datetime.fromtimestamp(epoch).date()
            if day == today:
                return "Today"
            if day == today + datetime.timedelta(days=1):
                return "Tomorrow"
            if day <= today + datetime.timedelta(days=7):
                return "This week"
            return "Later"

        try:
            from voice_assistant.core.gui_facade import scheduler as _sched
            getter = getattr(_sched, "get_all_reminders_with_epoch", None)
            _rows = list(getter() or []) if callable(getter) else []
        except Exception:  # noqa: BLE001 — no reminders then
            _rows = []
        for r in _rows or []:
            if not isinstance(r, dict):
                continue
            epoch = r.get("fire_at_epoch")
            epoch = (float(epoch) if isinstance(epoch, (int, float))
                     else None)
            label = str(r.get("label") or "")
            message = str(r.get("message") or "")
            fired = bool(r.get("fired"))
            events.append({
                "epoch": epoch,
                "group": _group(epoch),
                "kind_label": ("REMINDER · DAILY" if r.get("repeat")
                               else "REMINDER"),
                "title": message,
                # The label is the reminder's identity (Remove
                # selected cancels by it); when it differs from
                # the message it rides as a muted tag so the row
                # still names it.
                "tag": label if label and label != message else None,
                "time_text": (str(r.get("fire_at") or "")
                              if epoch is not None else "No time set"),
                "is_past": fired or (
                    epoch is not None and epoch < now_epoch),
                "on_select": (
                    (lambda _lbl=label:
                     self._select_timeline_reminder(_lbl))
                    if label else None),
            })
        try:
            from voice_assistant.core.gui_facade import planner as _planner
            routines = list(_planner.get_routines() or [])
        except Exception:  # noqa: BLE001 — no routines then
            routines = []
            _planner = None
        for routine in routines or []:
            if not isinstance(routine, dict):
                continue
            name = str(routine.get("name", "") or "Untitled")
            nxt = None
            if _planner is not None:
                try:
                    nxt = _planner.next_run(routine, now_local)
                except Exception:  # noqa: BLE001 — undated then
                    nxt = None
            epoch = (nxt.timestamp()
                     if isinstance(nxt, datetime.datetime) else None)
            events.append({
                "epoch": epoch,
                "group": _group(epoch),
                "kind_label": "ROUTINE",
                "title": name,
                "tag": None,
                "time_text": (self._routine_next_text(routine)
                              if epoch is not None else "No time set"),
                "is_past": epoch is not None and epoch < now_epoch,
                "on_select": None,
            })
        groups = {}
        for event in events:
            groups.setdefault(event["group"], []).append(event)
        for group in ("Today", "Tomorrow", "This week", "Later",
                      "No time set"):
            rows = groups.get(group)
            if not rows:
                continue
            rows.sort(key=lambda e: (
                e["epoch"] is None,
                e["epoch"] if e["epoch"] is not None else 0))
            header = QLabel(group.upper())
            header.setObjectName("muted")
            layout.addWidget(header)
            for event in rows:
                layout.addWidget(self._scheduled_timeline_row(event))
        if not events:
            # A-12 + B-A03: the timeline's empty state — the
            # resting ring + the tab's own line + one quiet
            # suggestion naming a real capability of the tab.
            from .design_system.empty import EmptyState
            layout.addWidget(EmptyState(
                sentence="No reminders yet.",
                suggested_task="Add a reminder below."))
        epochs = sorted(e["epoch"] for e in events
                        if e["epoch"] is not None)
        spine.set_epochs(epochs)
        spine.set_now(now_epoch)
        spine.setVisible(bool(epochs))

    def _reload_plugins(self):
        self._plugins = discover_plugins()
        if not hasattr(self, "plugin_list"):
            return
        self.plugin_list.clear()
        if not self._plugins:
            # Look-fix 2: the empty line is long enough to elide
            # under the list's ElideRight mode — the full guidance
            # rides the tooltip, so it is never lost.
            _empty = QListWidgetItem(
                "No extra skills yet. Skills a developer gives you "
                "go in the plugins folder.")
            _empty.setToolTip(
                "No extra skills yet. Skills a developer gives you "
                "go in the plugins folder.")
            self.plugin_list.addItem(_empty)
        else:
            for p in self._plugins:
                name = p.get("name", "?").replace("_", " ")
                desc = p.get("description", "")
                _item = QListWidgetItem(f"{name}  —  {desc}")
                _item.setToolTip(f"{name}  —  {desc}")
                self.plugin_list.addItem(_item)

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
