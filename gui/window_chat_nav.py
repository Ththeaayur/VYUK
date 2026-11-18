"""MainWindow chat mixin: navigation (GUI split #9, move only).

Page transitions, show, theme/chips/hero. Bodies moved verbatim from
gui/window_chat.py; behaviour is unchanged.
"""

from PySide6.QtCore import QParallelAnimationGroup, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QPushButton

from .motion import DURATION_PAGE_MS, DURATION_WINDOW_FADE_MS, EASE_PANEL
from .visualizer import THEMES
from .window_widgets import CommandPalette, ShortcutsSheet, _SlideOffset


class ChatNavMixin:
    def _snap_page_transition(self):
        """Force any in-flight page transition to its final state.

        Stops the animation group, removes the opacity effect and
        restores the page's exact layout position. Idempotent — the
        watchdog, rapid re-switches and the finished handler all
        funnel through here, so a page can never be left
        half-transparent or offset.
        """
        try:
            if getattr(self, "_page_watchdog", None) is not None:
                self._page_watchdog.stop()
        except Exception:  # noqa: BLE001
            pass
        anim = getattr(self, "_page_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:  # noqa: BLE001
                pass
            self._page_anim = None
        slide = getattr(self, "_page_slide", None)
        if slide is not None:
            try:
                slide.snap()
            except Exception:  # noqa: BLE001
                pass
            self._page_slide = None
        page = getattr(self, "_page_anim_page", None)
        if page is not None:
            try:
                page.setGraphicsEffect(None)
            except Exception:  # noqa: BLE001
                pass
            self._page_anim_page = None
    def _start_page_transition(self, page, direction: int = 0):
        """Fade + slide for the incoming page (~220 ms).

        Visual only: ``_show_page`` has already switched the stack
        synchronously, so callers and harnesses see the new page
        immediately. Any previous transition is snapped to final
        first — rapid switches never stack animations. v5.16:
        ``direction`` (+1/-1) slides the page in from the side you
        navigated toward, with a whisper of the original rise; 0
        keeps the pure original rise.
        """
        self._snap_page_transition()
        if page is None or not self._animate_enabled():
            return
        try:
            effect = QGraphicsOpacityEffect(page)
            effect.setOpacity(0.0)
            page.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b"opacity", self)
            fade.setDuration(DURATION_PAGE_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(EASE_PANEL)
            if direction:
                slide = _SlideOffset(page, self, dx=float(direction), dy=0.25)
                start = 16.0
            else:
                slide = _SlideOffset(page, self)
                start = 10.0
            slide._set_offset(start)
            rise = QPropertyAnimation(slide, b"offset", self)
            rise.setDuration(DURATION_PAGE_MS)
            rise.setStartValue(start)
            rise.setEndValue(0.0)
            rise.setEasingCurve(EASE_PANEL)
            group = QParallelAnimationGroup(self)
            group.addAnimation(fade)
            group.addAnimation(rise)
            group.finished.connect(self._snap_page_transition)
            self._page_anim = group
            self._page_slide = slide
            self._page_anim_page = page
            # Watchdog: if the group never finishes (interrupted
            # event loop, offscreen teardown), snap to final state
            # shortly after the animation should have ended.
            self._page_watchdog.start(420)
            group.start()
        except Exception:  # noqa: BLE001
            self._snap_page_transition()
    def _show_page(self, index: int):
        self.pages.setCurrentIndex(index)
        # `index` is a STACK page index. Nav buttons map through
        # _nav_page_indices when present (the world-view page
        # was removed 2026-11-05, so no button maps to stack 8
        # any more);
        # without it the mapping is identity, as before. Every
        # stack page has a button since rethink Build 3 (Skills
        # got one), so every page checks exactly its own button.
        _pages = getattr(self, "_nav_page_indices", None)
        for i, btn in enumerate(self.nav_buttons):
            _page = _pages[i] if _pages and i < len(_pages) else i
            btn.setChecked(_page == index)
        self._refresh_nav_icons(index)
        # Chat UI (2026-11-16): the composer's draft comes back
        # when the chat page is shown — a half-typed message
        # survives a page switch or a restart.
        if index == 0:
            try:
                self._chat_restore_composer_draft()
            except Exception:  # noqa: BLE001
                pass
        # Keep the offline-pack storage readout honest each time a page opens
        # (no-op until the Brain card has been built; cheap stat calls).
        self._refresh_pack_storage()
        # Same for the Your-world connection state (cheap local check).
        self._refresh_world_status()
        # Same for the Browser-control state (cheap local check).
        self._refresh_browser_enable_status()
        if index == 1:
            # Activity page: rebuild the scheduled-routines rows
            # too (they live in the Scheduled tab since S1).
            self._refresh_routines()
            # v5.49 (programme item 32): and the Plans rows.
            self._refresh_plans()
            # S1: and the In progress / History task tabs.
            self._refresh_activity_tabs()
        if index == 2:
            # Memory page: rebuild the learned-fact rows — and having
            # seen them, nothing on the page counts as New next time.
            self._refresh_learned_facts(mark_seen=True)
            # And the header count + hand-saved list: they are set
            # only by _refresh_memories, which nothing on the show
            # path called — so a fact learned since the window was
            # built left the header on its build-time "0 facts
            # remembered" while the card above already listed it.
            self._refresh_memories()
            # Facts learned in conversation since the page was last
            # open appear in the web too (hash-gated, so a no-change
            # visit costs nothing).
            self._refresh_memory_web()
        if index == 7:
            # v5.57 (redo): Doctor page — first visit runs the
            # checks; later visits show the last result until
            # Run again is pressed (idempotent).
            doctor_page = getattr(self, "doctor_page", None)
            if doctor_page is not None:
                doctor_page.ensure_initial_run()
        if index == 0:
            # v5.57 (redo): home — refresh the voice chip (the
            # combos exist by now) and the day briefing card.
            try:
                self._ensure_voice_chip_wiring()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._refresh_briefing_card()
            except Exception:  # noqa: BLE001
                pass
        # Premium Wave 2 + SENSES v5.16: incoming page fades/slides in
        # (~220 ms, ease-out), sliding from the direction of travel —
        # visual only, the switch above is synchronous.
        prev = getattr(self, "_page_index", index)
        self._page_index = index
        try:
            self._start_page_transition(
                self.pages.widget(index),
                direction=1 if index >= prev else -1)
        except Exception:  # noqa: BLE001
            pass
    # ------------------------------------------------------------ Phase 3
    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        # Launch fade-in: opacity 0 -> 1 over ~450 ms, once, guarded.
        if getattr(self, "_fade_started", False):
            return
        self._fade_started = True
        # v5.57 (redo): pin the chat at the frozen 400 px on first
        # show (the layout has run by now, so the splitter width
        # is final; the fade masks the one-frame adjustment).
        # Clear any pre-resize store a show-time resizeEvent left
        # behind, or its pending guard would undo this pin.
        try:
            self._fix_chat_width(first=True)
            self._chat_width_before_resize = None
        except Exception:  # noqa: BLE001 — the pin is a nicety
            pass
        # UX-safe batch: Motion Off skips the launch fade entirely
        # (same _animate_enabled() the page transitions consult).
        # The chat-width pin above still runs; only the animation
        # is skipped, and the final state is set immediately.
        if not self._animate_enabled():
            try:
                self.setWindowOpacity(1.0)
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            self.setWindowOpacity(0.0)
            anim = QPropertyAnimation(self, b"windowOpacity", self)
            anim.setDuration(DURATION_WINDOW_FADE_MS)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(EASE_PANEL)
            anim.finished.connect(lambda: self.setWindowOpacity(1.0))
            anim.start()
            self._fade_anim = anim
        except Exception:  # noqa: BLE001
            try:
                self.setWindowOpacity(1.0)
            except Exception:  # noqa: BLE001
                pass
    def _show_command_palette(self):
        try:
            if self._palette is None:
                self._palette = CommandPalette(self)
            self._palette.open_centered()
        except Exception:  # noqa: BLE001
            pass
    def _show_shortcuts_sheet(self):
        """v5.57 (redo, A9.2): the "?" sheet — cached like the
        palette, centred over the window on every open."""
        try:
            if getattr(self, "_shortcuts_sheet", None) is None:
                self._shortcuts_sheet = ShortcutsSheet(self)
            self._shortcuts_sheet.open_centered()
        except Exception:  # noqa: BLE001
            pass
    def _cycle_theme(self):
        try:
            names = list(THEMES.keys())
            current = getattr(self.visualizer, "theme_name", names[0])
            nxt = names[(names.index(current) + 1) % len(names)] \
                if current in names else names[0]
            self._on_theme_selected(nxt)
        except Exception:  # noqa: BLE001
            pass
    def _refresh_chip_row(self):
        """Rebuild the contextual chip row for the current chat
        state (Phase 2, chatbox #18).

        Chat UI (2026-11-16): while a reply streams the row is
        NOT rebuilt (move 15 — the transcript is the only thing
        that moves); and the empty chat teaches with exactly
        three starters (move 14), not seven — three things to
        try, not a menu to read."""
        # The quiet rule: a streaming reply freezes the chips.
        if getattr(self, "_chat_quiet_streaming", False):
            return
        layout = getattr(self, "_chip_layout", None)
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            wdg = item.widget()
            if wdg is not None:
                # Hide now: leaving a layout does not hide a
                # widget, and deleteLater alone would leave the
                # old chips painted until the event loop collects
                # them.
                wdg.setVisible(False)
                wdg.deleteLater()
        layout.addStretch()
        if getattr(self, "_chat_has_messages", False):
            items = [
                ("What can you do?",
                 lambda: self._on_suggestion_clicked(
                     "What can you do?")),
                ("What's on my screen?",
                 lambda: self._on_suggestion_clicked(
                     "What's on my screen?")),
                ("Take a screenshot",
                 lambda: self._on_quick_action("Take a screenshot")),
                ("Remind me in 10 min",
                 lambda: self._on_suggestion_clicked(
                     "Remind me in 10 min")),
            ]
        else:
            items = [
                ("What can you do?",
                 lambda: self._on_suggestion_clicked(
                     "What can you do?")),
                ("Remind me in 10 min",
                 lambda: self._on_suggestion_clicked(
                     "Remind me in 10 min")),
                ("What's on my screen?",
                 lambda: self._on_suggestion_clicked(
                     "What's on my screen?")),
            ]
        for label, fn in items:
            chip = QPushButton(label)
            chip.setObjectName("chipSuggest")
            chip.clicked.connect(lambda _=False, f=fn: f())
            layout.addWidget(chip)
        layout.addStretch()
        # Split layout: the row lives in a non-resizable scroll
        # strip (see _build_assistant_page) — keep its width at
        # its natural size after every rebuild so the chips
        # slide sideways instead of squeezing to nothing.
        # v5.52 (item 1): the one-shot sizeHint() resize that
        # used to sit here was the bug — measured before fonts
        # settled, it froze a too-narrow width and the layout
        # centre-clipped every label on scaled displays. The
        # fit now comes from _ChipRow.fit_chips(): per-chip
        # minimum widths from LIVE font metrics + the row sized
        # to the layout's true total, re-applied on every
        # rebuild and on font/style changes (changeEvent).
        try:
            self._suggest_row.fit_chips()
        except Exception:  # noqa: BLE001 — cosmetics never break chat
            pass
    def _sync_send_button(self):
        """Send becomes Stop while a turn is in flight (move 2):
        one button, one place, always visible — a square glyph
        for Stop, the send icon for Send. Stop is enabled even
        with an empty composer (there is a reply to stop); Send
        needs text, as before."""
        btn = getattr(self, "btn_send", None)
        if btn is None:
            return
        mode = "send"
        try:
            mode = self._chat_send_mode()
        except Exception:  # noqa: BLE001 — a missing mixin reads as Send
            pass
        if mode == "stop":
            try:
                # The send icon is cached on first use, so the
                # Stop flip never re-imports or re-renders it.
                if not hasattr(btn, "_chat_send_icon"):
                    btn._chat_send_icon = btn.icon()
                from PySide6.QtGui import QIcon as _QIcon
                btn.setIcon(_QIcon())
                btn.setText("■")
            except Exception:  # noqa: BLE001
                btn.setText("■")
            btn.setToolTip("Stop reply")
            btn.setAccessibleName("Stop reply")
            btn.setAccessibleDescription(
                "Stop the reply in progress")
            btn.setEnabled(True)
            return
        try:
            btn.setText("")
            btn.setIcon(getattr(btn, "_chat_send_icon", btn.icon()))
        except Exception:  # noqa: BLE001
            btn.setText("")
        btn.setToolTip("Send (Enter)")
        btn.setAccessibleName("Send message")
        btn.setAccessibleDescription(
            "Send the typed message")
        btn.setEnabled(bool(self.input_cmd.text().strip()))
    def _set_hero_collapsed(self, collapsed: bool):
        """Split layout (v5.34, Aayur 2026-10-22): RETIRED as a
        visual behaviour. The Phase-2 fold existed to give the
        transcript room; the transcript has its own side panel
        now, so the stage keeps the full hero for the whole
        conversation — orb in the centre, always. The method
        stays as the record of conversation state its callers
        (first message, clear chat) still report."""
        self._hero_collapsed = bool(collapsed)
    def _mark_chat_has_messages(self):
        was_empty = not getattr(self, "_chat_has_messages", False)
        self._chat_has_messages = True
        if was_empty:
            # The chip row switches from starters to follow-ups —
            # and stays visible (Phase 2, chatbox #18).
            self._refresh_chip_row()
            self._set_hero_collapsed(True)
    def _on_suggestion_clicked(self, text: str):
        # Route through the existing typed-send path verbatim.
        self._mark_chat_has_messages()
        try:
            self.input_cmd.setText(text)
        except Exception:  # noqa: BLE001
            pass
        self._send_typed_message()
    def _on_quick_action(self, command: str):
        # Same real path as _on_suggestion_clicked: the command goes to
        # Gemini exactly as if it were typed. Offline, _send_typed_message
        # answers with its usual "not connected yet" system line, so the
        # tap always gets honest feedback instead of failing silently.
        self._mark_chat_has_messages()
        try:
            self.input_cmd.setText(command)
        except Exception:  # noqa: BLE001
            pass
        self._send_typed_message()
