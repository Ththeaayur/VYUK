# Refactor Phase 1 step 5: ShellMixin panel mixin, moved verbatim from gui/window_shell.py.
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QFileDialog,
    QMenu,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSplitter,
    QSplitterHandle,
    QStackedLayout,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)
from voice_assistant.core.gui_facade import scheduler
from PySide6.QtCore import (
    QEvent,
    QRectF,
    QSettings,
    QSize,
    QThread,
    Qt,
    QTimer,
    Signal,
)



class _CalendarWorker(QThread):
    """v5.57 (redo): today's calendar for the briefing card, off
    the GUI thread — a slow calendar must never stall the page.
    Emits (events, ok): ok is False only when the fetch itself
    raised; an empty list on a connected account means nothing
    coming up (next_events' own contract — it never raises)."""

    done = Signal(list, bool)

    def run(self):
        try:
            from voice_assistant.core.gui_facade import google_services
            events = google_services.next_events(within_hours=24,
                                                 limit=20)
            self.done.emit(events, True)
        except Exception:  # noqa: BLE001
            self.done.emit([], False)


# Field-check fix #8 (U5 #34): the briefing card's narrow
# width. Below this card width the Play briefing button
# collapses to icon-only (see _apply_briefing_narrow_rule).
# Measured: at the 420 px stage floor the card is 320 wide
# and the summary had 107 px ("Calendar is…"); at a 1024 px
# window the card is 436 wide and the summary reads. 420 sits
# between the two.
_BRIEFING_NARROW_WIDTH = 420


class ShellBriefingMixin:


    def _show_header_overflow_menu(self):
        """UIS2 (home simplification, CX-02): the conversation
        overflow menu — the new home of the header controls
        that left the header. "Export chat" keeps its handler
        (_export_chat); microphone health shows its live words
        (the dot's tooltip, rendered by _refresh_mic_health on
        the telemetry tick) as a read-only entry. Privacy
        indicators deliberately do NOT live here: watch and
        capture pills stay in the header, always visible while
        their sensors are active, never behind a menu."""
        menu = QMenu(self)
        act_export = menu.addAction("Export chat")
        act_export.setToolTip("Save this conversation as a readable file")
        act_export.triggered.connect(self._export_chat)
        # UI rethink Build 1: Conversations moved here from the chat
        # header row — the day list lives in the overflow, so the
        # header row hides when it has nothing to show. The dialog
        # itself is unchanged.
        act_conversations = menu.addAction("Conversations…")
        act_conversations.setToolTip("Past conversations, by day")
        act_conversations.triggered.connect(
            self._chat_open_conversations)
        # UIS5 (composer narrow rule): the dock's screen-share
        # button hides at narrow panel widths, so the action
        # lives here too — reachable at every width.
        act_screen = menu.addAction("Share screen")
        act_screen.setToolTip("Share your screen with VYUK")
        act_screen.triggered.connect(self._send_screen_capture)
        # Sci-fi Home: the voice/language chip left the stage, so
        # its menu lives here — the same menu, the same handlers,
        # opened from the overflow button instead of the chip.
        act_voice = menu.addAction("Voice & language…")
        act_voice.setToolTip("Switch VYUK's voice or reply language")
        act_voice.triggered.connect(self._show_voice_chip_menu)
        menu.addSeparator()
        _health_words = {
            "no_stream": "No stream yet",
            "healthy": "Stream held — no drops",
            "dropping": "Dropping sound — speech may have gaps",
        }
        _key = getattr(self, "_mic_health_key", "no_stream")
        _dot = getattr(self, "mic_health_dot", None)
        _tip = ""
        try:
            _tip = _dot.toolTip() if _dot is not None else ""
        except Exception:  # noqa: BLE001 — menu must still open
            _tip = ""
        act_health = menu.addAction(
            f"Microphone health: {_health_words.get(_key, _key)}")
        act_health.setToolTip(_tip)
        act_health.setEnabled(False)
        menu.exec(self.btn_header_overflow.mapToGlobal(
            self.btn_header_overflow.rect().bottomLeft()))

    # ------------------------------------------------- v5.57 home additions
    def _refresh_voice_chip(self):
        """Voice chip text from the Settings combos, verbatim —
        the same terms as Settings (A9.4). Missing combos (the
        chip is built before the Settings page) leave the neutral
        placeholder until home show wires it up."""
        voice = None
        lang = None
        try:
            combo_voice = getattr(self, "combo_voice", None)
            if combo_voice is not None:
                voice = combo_voice.currentText() or None
            combo_lang = getattr(self, "combo_language", None)
            if combo_lang is not None:
                lang = combo_lang.currentText() or None
        except Exception:  # noqa: BLE001
            pass
        if voice and lang:
            text = f"● {voice} · {lang} ▾"
        elif voice or lang:
            text = f"● {voice or lang} ▾"
        else:
            text = "Voice & language ▾"
        try:
            self.voice_chip.setText(text)
        except Exception:  # noqa: BLE001
            pass


    def _ensure_voice_chip_wiring(self):
        """Connect the chip's refresh to the Settings combos once
        (they exist only after the Settings page is built)."""
        if getattr(self, "_voice_chip_wired", False):
            self._refresh_voice_chip()
            return
        try:
            combo_voice = getattr(self, "combo_voice", None)
            if combo_voice is not None:
                combo_voice.currentTextChanged.connect(
                    self._refresh_voice_chip)
            combo_lang = getattr(self, "combo_language", None)
            if combo_lang is not None:
                combo_lang.currentIndexChanged.connect(
                    self._refresh_voice_chip)
            self._voice_chip_wired = True
        except Exception:  # noqa: BLE001
            pass
        self._refresh_voice_chip()


    def _show_voice_chip_menu(self):
        """The chip's menu: Voice + Language submenus driving the
        existing Settings combos (language picks also call
        _on_language_picked, since `activated` never fires on a
        programmatic index change), plus a way into Settings."""
        try:
            menu = QMenu(self)
            combo_voice = getattr(self, "combo_voice", None)
            if combo_voice is not None:
                voice_menu = menu.addMenu("Voice")
                for i in range(combo_voice.count()):
                    act = voice_menu.addAction(combo_voice.itemText(i))
                    act.setCheckable(True)
                    act.setChecked(i == combo_voice.currentIndex())
                    act.triggered.connect(
                        lambda _=False, idx=i: combo_voice.setCurrentIndex(idx))
            combo_lang = getattr(self, "combo_language", None)
            if combo_lang is not None:
                lang_menu = menu.addMenu("Language")
                for i in range(combo_lang.count()):
                    act = lang_menu.addAction(combo_lang.itemText(i))
                    act.setCheckable(True)
                    act.setChecked(i == combo_lang.currentIndex())
                    act.triggered.connect(
                        lambda _=False, idx=i: (
                            combo_lang.setCurrentIndex(idx),
                            self._on_language_picked(idx)))
            menu.addSeparator()
            settings_act = menu.addAction("Voice settings…")
            settings_act.triggered.connect(
                lambda: self.show_settings_card("Voice"))
            # Sci-fi Home: the chip is hidden now, so the menu
            # anchors on the overflow button that opened it (the
            # chip's own geometry is no longer on screen).
            anchor = getattr(self, "btn_header_overflow", None)
            if anchor is None:
                anchor = self.voice_chip
            menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))
        except Exception:  # noqa: BLE001 — the chip never breaks home
            pass

    def _set_briefing_schedule_text(self, text):
        """Set the schedule line AND the collapsed card's summary.

        Every render path funnels through here, so the one-line
        summary can never drift from the full schedule."""
        self.briefing_schedule.setText(text)
        self._set_briefing_summary(text)

    def _set_briefing_summary(self, text):
        """Set the collapsed card's one line from the schedule
        line (see room_home.briefing_summary_for). The full text
        is kept so a resize can re-elide it; the label shows the
        elided form against its current width. No-op on windows
        without the summary label (the surfacing tests' fakes)."""
        summary = getattr(self, "briefing_summary", None)
        if summary is None:
            return
        try:
            from voice_assistant.gui.room_home import (
                briefing_summary_for)
            full = briefing_summary_for(text)
        except Exception:  # noqa: BLE001 — summary never breaks home
            full = text
        self._briefing_summary_full = full
        self._reelide_briefing_summary()

    def _reelide_briefing_summary(self):
        """Re-apply the one-line elision against the label's
        current width (called after a resize, and on every set)."""
        summary = getattr(self, "briefing_summary", None)
        full = getattr(self, "_briefing_summary_full", None)
        if summary is None or full is None:
            return
        try:
            from PySide6.QtGui import QFontMetrics
            width = summary.width()
            if width > 0:
                elided = QFontMetrics(summary.font()).elidedText(
                    full, Qt.TextElideMode.ElideRight, width)
                # UI rethink Build 1: elide at a word boundary —
                # the old cut left "Y…" / "Setting…" mid-word. If
                # the cut fell mid-word, back up to the last space
                # and strip any trailing separator. A first word
                # too long for the label keeps the character cut.
                if elided.endswith("…") and len(elided) < len(full):
                    visible = elided[:-1]
                    if (len(full) > len(visible)
                            and full[len(visible)] not in (" ", "·")):
                        cut = visible.rfind(" ")
                        if cut > 0:
                            visible = visible[:cut]
                    visible = visible.rstrip(" ·").rstrip()
                    summary.setText(visible + "…" if visible else elided)
                else:
                    summary.setText(elided)
            else:
                summary.setText(full)
        except Exception:  # noqa: BLE001 — cosmetic
            try:
                summary.setText(full)
            except Exception:  # noqa: BLE001
                pass
        # Field-check fix #8 (U5 #34): the narrow rule — a state
        # change shifts the layout, so re-elide once it settles
        # (the Build 1 deferred pattern); the second pass sees
        # the state already right and schedules nothing.
        try:
            if self._apply_briefing_narrow_rule():
                QTimer.singleShot(0, self._reelide_briefing_summary)
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _apply_briefing_narrow_rule(self) -> bool:
        """UIS5 narrow rule for the briefing card header.

        Below _BRIEFING_NARROW_WIDTH (420) of card width, the
        Play briefing button collapses to icon-only so the
        one-line summary keeps a readable width: at the 420 px
        stage floor the card is 320 wide and the summary had
        107 px ("Calendar is…"); icon-only frees ~90 px and the
        summary reads ~30 characters instead of two words. The
        icon, tooltip and accessible name stay, so the action
        is unchanged — only its width. At or above the
        threshold the text comes back. The card width drives
        the rule (not the window width): the splitter, not the
        window, decides how wide the card is. Returns True
        when the button state changed (the caller re-elides
        once the layout settles). No-op before the card exists
        or before it has a width.
        """
        card = getattr(self, "briefing_card", None)
        btn = getattr(self, "btn_briefing", None)
        if card is None or btn is None:
            return False
        if card.width() <= 0:
            return False
        narrow = card.width() < _BRIEFING_NARROW_WIDTH
        collapsed = btn.text() == ""
        if narrow == collapsed:
            return False
        if narrow:
            btn.setText("")
        else:
            btn.setText("Play briefing")
        btn.setAccessibleName("Play briefing")
        btn.setToolTip("Play briefing")
        return True

    def _toggle_briefing_card(self):
        """Expand / collapse the day briefing card in place. The
        summary stays visible either way; the chevron and its
        accessible name track the state."""
        expanded = not getattr(self, "_briefing_expanded", False)
        self._briefing_expanded = expanded
        details = getattr(self, "_briefing_details", None)
        if details is not None:
            details.setVisible(expanded)
        btn = getattr(self, "btn_briefing_expand", None)
        if btn is not None:
            btn.setText("▴" if expanded else "▾")
            label = ("Hide the full day briefing" if expanded
                     else "Show the full day briefing")
            btn.setAccessibleName(label)
            btn.setToolTip(label)
        # B7F1 (visual review Finding 1): on expand, scroll the
        # stage once so the expanded card is fully visible — the
        # kicker + schedule line sat below the fold at 1280x800
        # (content 610 px vs a 551 px viewport) and the user had
        # to scroll by hand. Collapse schedules nothing, and
        # first paint never calls this handler. Deferred one
        # tick (the Build 1 pattern): the layout must settle
        # after the details show before geometry is read.
        if expanded:
            QTimer.singleShot(0, self._scroll_briefing_card_into_view)

    def _scroll_briefing_card_into_view(self):
        """One-shot scroll after the briefing card expands.

        Scrolls the stage down only as far as needed to bring
        the card's bottom to the viewport's bottom — never up
        (a card already fully visible stays put), never past
        the scrollbar's maximum (at 1024x768 that is as far as
        the card can come). Fires once per tap; nothing watches
        afterwards, so it cannot fight the user's own scrolling.
        No-op if the card was collapsed again before the timer
        fired, or on windows without the stage scroll area.
        """
        if not getattr(self, "_briefing_expanded", False):
            return
        scroll = getattr(self, "_stage_scroll", None)
        card = getattr(self, "briefing_card", None)
        if scroll is None or card is None:
            return
        bar = scroll.verticalScrollBar()
        viewport_h = scroll.viewport().height()
        if viewport_h <= 0:
            return
        # The card is a direct child of the scroll area's widget,
        # so card.y() is already in content coords.
        desired = card.y() + card.height() - viewport_h
        if desired > bar.value():
            bar.setValue(min(desired, bar.maximum()))


    def _refresh_briefing_card(self):
        """Fill the day briefing card — greeting + schedule +
        reminders + battery. Local parts render at once; the
        calendar fills in from the worker (or the honest line
        when Google isn't connected)."""
        if not hasattr(self, "briefing_kicker"):
            return
        try:
            self.briefing_kicker.setText(self._greeting().upper())
        except Exception:  # noqa: BLE001
            pass
        try:
            state = self._senses_state()
            pct = state.get("battery_pct")
            plugged = state.get("battery_plugged")
            if pct is None:
                self.briefing_battery.setVisible(False)
            else:
                self.briefing_battery.setVisible(True)
                self.briefing_battery.setText(
                    f"Battery {pct:.0f}% · "
                    f"{'Plugged in' if plugged else 'On battery'}")
        except Exception:  # noqa: BLE001
            try:
                self.briefing_battery.setVisible(False)
            except Exception:  # noqa: BLE001
                pass
        try:
            self._refresh_briefing_second_opinion()
        except Exception:  # noqa: BLE001 — briefing never breaks
            pass
        try:
            connected = bool(self._google_connected())
        except Exception:  # noqa: BLE001
            connected = False
        if not connected:
            self._render_briefing_schedule(None, ok=True,
                                           connected=False)
            return
        worker = getattr(self, "_briefing_worker", None)
        if worker is not None and worker.isRunning():
            self._render_briefing_schedule(None, ok=True,
                                           connected=True)
            return
        worker = _CalendarWorker(self)
        worker.done.connect(self._on_briefing_calendar_done)
        worker.finished.connect(worker.deleteLater)
        self._briefing_worker = worker
        worker.start()
        self._render_briefing_schedule(None, ok=True, connected=True)


    def _refresh_briefing_second_opinion(self):
        """Show the latest task's second-opinion OFF line, if any.

        B24: the home briefing carries the same OFF line as the
        receipt/ledger/Activity — the most recent task record
        that recorded one (latest_second_opinion_off_line over
        the ledger's records). A label with no line hides, so
        a task whose check ran (or no task yet) reads exactly
        as before. Read-only ledger load; never raises — the
        briefing never breaks for a task line.
        """
        label = getattr(self, "briefing_second_opinion", None)
        if label is None:
            return
        line = ""
        try:
            from voice_assistant.core.gui_facade import TaskLedger
            from voice_assistant.gui.task_state import (
                latest_second_opinion_off_line)
            line = latest_second_opinion_off_line(
                TaskLedger().all_records())
        except Exception:  # noqa: BLE001 — briefing never breaks
            line = ""
        try:
            if line:
                label.setText(line)
                label.setVisible(True)
            else:
                label.setVisible(False)
        except Exception:  # noqa: BLE001
            pass

    def _on_briefing_calendar_done(self, events, ok: bool):
        self._briefing_worker = None
        self._render_briefing_schedule(events if ok else None, ok=ok,
                                       connected=True)


    def _set_connect_calendar_visible(self, visible: bool) -> None:
        """Show the "Connect calendar" row (the details' first row)
        only while the calendar is not connected. The button is
        created in _build_assistant_page; surfacing fakes may not
        have it."""
        btn = getattr(self, "btn_connect_calendar", None)
        if btn is not None:
            btn.setVisible(visible)

    def _render_briefing_schedule(self, events, ok: bool = True,
                                  connected: bool = True):
        """One schedule line for the briefing card. events is None
        while the calendar is still loading; ok False means the
        fetch itself raised (the failure line, with a next step)."""
        # UI rethink Build 1: the Connect-calendar row shows only
        # in the not-connected branch below; every other branch
        # hides it (a failed fetch is not fixed by connecting).
        self._set_connect_calendar_visible(False)
        try:
            reminders = [r for r in scheduler.get_all_reminders()
                         if not r["fired"]]
        except Exception:  # noqa: BLE001
            reminders = []

        def _names(items):
            shown = list(items[:3])
            extra = len(items) - 3
            if extra > 0:
                shown.append(f"+{extra} more")
            return " · ".join(shown)

        if not ok:
            self._set_briefing_schedule_text(
                "Today's calendar couldn't be loaded — press Play "
                "briefing and ask her.")
            return
        if not connected:
            # UI rethink Build 1: one short sentence on the bar —
            # the "connect it in Settings" instruction is the
            # Connect calendar row behind the chevron, not a
            # sentence on the bar.
            parts = ["Calendar not connected"]
            if reminders:
                parts.append(
                    f"Reminders: {_names([r['message'] for r in reminders])}")
            self._set_briefing_schedule_text(" · ".join(parts))
            self._set_connect_calendar_visible(True)
            return
        if events is None:
            if reminders:
                self._set_briefing_schedule_text(
                    "Checking today's calendar… · Reminders: "
                    f"{_names([r['message'] for r in reminders])}")
            else:
                self._set_briefing_schedule_text(
                    "Checking today's calendar…")
            return
        parts = []
        if events:
            titles = []
            for ev in events[:3]:
                title = ev.get("title") or "(untitled event)"
                start = ev.get("start")
                if start is not None:
                    try:
                        titles.append(f"{title} {start.strftime('%H:%M')}")
                    except Exception:  # noqa: BLE001
                        titles.append(title)
                else:
                    titles.append(title)
            if len(events) > 3:
                titles.append(f"+{len(events) - 3} more")
            parts.append(" · ".join(titles))
        if reminders:
            parts.append(
                f"Reminders: {_names([r['message'] for r in reminders])}")
        if not parts:
            self._set_briefing_schedule_text(
                "Nothing scheduled today — ask me to add something.")
        else:
            total = len(events) + len(reminders)
            self._set_briefing_schedule_text(
                f"{total} things coming up · " + " · ".join(parts))
        # Conversion Wave 9: refresh the on-this-day line whenever
        # the briefing schedule renders (every render path funnels
        # here, including the calendar worker's completion).
        try:
            from .w9_wiring_proof import update_briefing_on_this_day
            update_briefing_on_this_day(self)
        except Exception:  # noqa: BLE001 — briefing never breaks
            pass
