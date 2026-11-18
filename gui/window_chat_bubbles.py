"""MainWindow chat mixin: bubbles (GUI split #9, move only).

Thinking bubbles, find-in-chat, scroll/width, eventFilter, fade,
bubbles/cards. Bodies moved verbatim from gui/window_chat.py;
behaviour is unchanged.
"""

import datetime

from PySide6.QtCore import (
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QFontMetrics
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import memory_store

from .icons import make_icon
from .motion import DURATION_QUICK_MS, EASE_PANEL
from .style import ACCENT_2, AMBER
from .window_widgets import (
    ChatBubble,
    ThinkingDots,
    _DIMMED_OPACITY,
    _SlideOffset,
    _md_lite,
    _title_case,
    _tool_icon_name,
)


class ChatBubblesMixin:
    def _show_thinking_bubble(self):
        if getattr(self, "_thinking_row", None) is not None:
            return
        if not hasattr(self, "chat_layout"):
            return
        try:
            row = QHBoxLayout()
            bubble = QFrame()
            bubble.setObjectName("bubbleNova")
            bubble.setMaximumWidth(560)
            # Phase 2 (chatbox #13): the placeholder follows the
            # same width rule as a reply, so the swap doesn't snap
            # the column wider.
            bubble.setFixedWidth(max(170, min(self._chat_width_cap(), 380)))
            b_layout = QVBoxLayout(bubble)
            b_layout.setContentsMargins(14, 12, 14, 12)
            b_layout.setSpacing(2)
            who_lbl = QLabel(config.assistant_name)
            who_lbl.setObjectName("bubbleWho")
            dots = ThinkingDots()
            try:
                dots.set_accent(self.visualizer.accent_color())
            except Exception:  # noqa: BLE001
                pass
            b_layout.addWidget(who_lbl)
            b_layout.addWidget(dots)
            row.addWidget(bubble)
            row.addStretch()
            self.chat_layout.addLayout(row)
            self._thinking_row = row
            self._thinking_bubble = bubble
            self._thinking_dots = dots
            self._thinking_frame = 0
            self._fade_in(bubble)
            self._thinking_timer.start()
            self._scroll_chat_to_bottom()
        except Exception:  # noqa: BLE001
            self._thinking_row = None
            self._thinking_bubble = None
            self._thinking_dots = None
    def _hide_thinking_bubble(self):
        try:
            self._thinking_timer.stop()
        except Exception:  # noqa: BLE001
            pass
        row = getattr(self, "_thinking_row", None)
        if row is None:
            return
        self._thinking_row = None
        self._thinking_bubble = None
        self._thinking_dots = None
        try:
            self.chat_layout.removeItem(row)
            while row.count():
                item = row.takeAt(0)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.deleteLater()
        except Exception:  # noqa: BLE001
            pass
    def _advance_thinking_dots(self):
        dots = getattr(self, "_thinking_dots", None)
        if dots is None:
            return
        self._thinking_frame += 1
        try:
            dots.set_phase(self._thinking_frame)
            # Keep the dots in the orb's current accent as themes crossfade.
            dots.set_accent(self.visualizer.accent_color())
        except Exception:  # noqa: BLE001
            pass
    # ------------------------------------------------------------ chat feed
    # -- v5.31: find in chat -----------------------------------
    def _open_chat_find(self):
        if self.pages.currentIndex() != 0:
            self._show_page(0)
        self._find_bar.setVisible(True)
        self.find_input.setFocus()
        self.find_input.selectAll()
        self._chat_find_reset(self.find_input.text())
    def _close_chat_find(self):
        self._find_bar.setVisible(False)
        self._clear_find_glow()
        self.input_cmd.setFocus()
    def _chat_bubbles(self) -> list:
        """Every ChatBubble in the chat layout, in order
        (bubbles live inside per-row layouts, so walk the
        layout tree, not just the top level)."""
        out = []

        def _walk(layout):
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item is None:
                    continue
                widget = item.widget()
                if isinstance(widget, ChatBubble):
                    out.append(widget)
                elif widget is not None:
                    out.extend(widget.findChildren(ChatBubble))
                sub = item.layout()
                if sub is not None:
                    _walk(sub)

        _walk(self.chat_layout)
        return out
    def _chat_find_reset(self, text: str):
        """Find across the whole history, not just the loaded
        bubbles (chat UI, 2026-11-16): the loaded bubbles are
        searched as before, and the store is searched too —
        matches already on screen are deduped by their stored
        row id, so each match is counted once. The count reads
        "N matches (M in older history)" when some matches live
        only in the store. Walking order is time order: the
        older stored matches first, then the loaded bubbles in
        view order."""
        query = (text or "").strip().lower()
        self._find_matches = []
        self._find_index = -1
        self._find_store_total = 0
        if query:
            _loaded_ids = set()
            # Tree accessor looked up, not called — see
            # _add_bubble. Without a tree there is nothing to
            # dedupe against, and find still returns the loaded
            # matches.
            _thread_get = getattr(self, "_chat_thread", None)
            _thread = (_thread_get()
                       if _thread_get is not None else None)
            for bubble in self._chat_bubbles():
                plain = getattr(bubble, "_plain_text", "") or ""
                if query in plain.lower():
                    self._find_matches.append(("bubble", bubble))
                    if _thread is None:
                        continue
                    _node = _thread.get(
                        getattr(bubble, "_chat_msg_id", None) or -1)
                    if _node is not None and _node.store_id is not None:
                        _loaded_ids.add(_node.store_id)
            try:
                _stored = memory_store.search_history(query, 50) or []
            except Exception:  # noqa: BLE001 — find never breaks on the store
                _stored = []
            _older = [r for r in _stored
                      if r.get("id") not in _loaded_ids]
            self._find_store_total = len(_older)
            _older.sort(key=lambda r: r.get("id") or 0)
            # Stored matches (older) walk before the loaded ones.
            self._find_matches = (
                [("store", r) for r in _older]
                + [m for m in self._find_matches if m[0] == "bubble"])
        total = len(self._find_matches)
        _older_n = getattr(self, "_find_store_total", 0)
        if total:
            _label = (f"{total} match{'es' if total != 1 else ''}")
            if _older_n:
                _label += (f" ({_older_n} in older history)")
            self.find_count.setText(_label)
        else:
            self.find_count.setText(
                "No matches" if query else "")
        if total:
            self._chat_find_step(1)

    def _chat_find_step(self, delta: int):
        if not self._find_matches:
            return
        self._find_index = (
            (self._find_index + delta) % len(self._find_matches))
        kind, target = self._find_matches[self._find_index]
        self.find_count.setText(
            f"{self._find_index + 1} of {len(self._find_matches)}")
        if kind == "bubble":
            self.chat_scroll.ensureWidgetVisible(target, 0, 40)
            self._glow_bubble(target)
            return
        # A stored match: load its surroundings as a history
        # window and glow the match there. The find state is
        # spent by the jump — the window is a new view, and the
        # find bar's matches belong to the live thread.
        try:
            _rows = memory_store.get_history_around(
                target.get("id"), before=10, after=10) or []
        except Exception:  # noqa: BLE001 — find never breaks on the store
            _rows = []
        self._find_matches = []
        self._find_index = -1
        self.find_count.setText("")
        if _rows:
            self._chat_open_history_window(
                _rows, match_id=target.get("id"))
    def _clear_find_glow(self, token=None):
        # Stale-timer guard (fix #31 follow-up): every glow arms
        # its own 1200 ms timer, and a jump to a newer match
        # replaces the glow before the older timer fires. A timer
        # whose token is not the current one belongs to a glow
        # that a newer jump already replaced — it must not clear
        # the newer glow's effect (or drop its ref). A call with
        # no token (view clear, direct clear, find close) still
        # clears unconditionally, as before.
        if (token is not None
                and token != getattr(self, "_find_glow_token", None)):
            return
        bubble = getattr(self, "_glowing_bubble", None)
        if bubble is not None:
            try:
                bubble.setGraphicsEffect(None)
            except RuntimeError:
                # Round-2 N6: the bubble was deleted by a view
                # clear (e.g. Back to latest) after the 1200 ms
                # glow timer was set — the effect dies with it;
                # there is nothing left to clear. The ref is
                # still dropped below, so a later timer (or a
                # direct call) is a no-op, never a second raise.
                pass
            self._glowing_bubble = None
    def _glow_bubble(self, bubble):
        self._clear_find_glow()
        effect = QGraphicsDropShadowEffect(bubble)
        effect.setColor(QColor(AMBER))
        effect.setBlurRadius(24)
        effect.setOffset(0, 0)
        bubble.setGraphicsEffect(effect)
        self._glowing_bubble = bubble
        token = getattr(self, "_find_glow_token", 0) + 1
        self._find_glow_token = token
        QTimer.singleShot(1200, lambda: self._clear_find_glow(token))
    def _scroll_chat_to_bottom(self):
        # Phase 2 (chatbox #10): only follow the conversation when
        # the user is already near the bottom — someone reading back
        # through history is never yanked down; a pill offers the
        # jump instead.
        bar = self.chat_scroll.verticalScrollBar()
        if getattr(self, "_chat_near_bottom", True):
            QTimer.singleShot(30, lambda: bar.setValue(bar.maximum()))
        else:
            pill = getattr(self, "_btn_new_msgs", None)
            if pill is not None:
                try:
                    # Chat UI (2026-11-16): the pill carries the
                    # count — "3 new messages" — so the user knows
                    # how much arrived while they were reading.
                    # The pill never pulses (move 15): it appears
                    # and sits still.
                    _n = getattr(self, "_new_msgs_since", 0)
                    if _n > 0:
                        pill.setText(
                            f"↓ {_n} new message{'s' if _n != 1 else ''}")
                    else:
                        pill.setText("↓ New messages")
                    vp = self.chat_scroll.viewport()
                    pill.move(max(8, (vp.width() - pill.width()) // 2),
                              max(8, vp.height() - pill.height() - 12))
                    pill.setVisible(True)
                    pill.raise_()
                except Exception:  # noqa: BLE001
                    pass

    def _mark_first_new_message(self):
        """The hairline above the first message that arrived
        while the user was reading back (move 10): on the jump
        to latest, the boundary is marked so the new messages
        are findable by eye. Only one such marker exists at a
        time; it is a view marker, never a tree node."""
        try:
            from PySide6.QtCore import Qt as _Qt
            from PySide6.QtWidgets import QLabel as _QLabel
            msg_id = getattr(self, "_chat_first_new_msg_id", None)
            if msg_id is None:
                return
            old = getattr(self, "_chat_new_marker", None)
            if old is not None:
                try:
                    self.chat_layout.removeWidget(old)
                    old.deleteLater()
                except Exception:  # noqa: BLE001
                    pass
                self._chat_new_marker = None
            bubble = self._chat_bubble_for(msg_id)
            if bubble is None:
                return
            idx = self._chat_row_index_for(bubble)
            if idx is None:
                return
            lbl = _QLabel("New messages")
            lbl.setObjectName("chatDivider")
            lbl.setAlignment(_Qt.AlignmentFlag.AlignCenter)
            self.chat_layout.insertWidget(idx, lbl)
            self._chat_new_marker = lbl
        except Exception:  # noqa: BLE001
            pass
    def _chat_width_cap(self) -> int:
        """Bubbles and cards may use at most ~78% of the chat
        viewport (Phase 2, chatbox #4) — never a fixed 560 px
        regardless of the window."""
        try:
            vw = self.chat_scroll.viewport().width()
        except Exception:  # noqa: BLE001
            vw = 720
        return max(200, min(560, int(vw * 0.78)))
    def _fit_chat_item(self, widget):
        cw = widget.property("content_w")
        if cw:
            widget.setFixedWidth(
                max(150, min(int(cw), self._chat_width_cap())))
    def _refit_chat_widths(self):
        content = getattr(self, "chat_scroll", None)
        if content is None:
            return
        inner = content.widget()
        if inner is None:
            return
        cap = self._chat_width_cap()
        for frame in inner.findChildren(QFrame):
            cw = frame.property("content_w")
            if cw:
                frame.setFixedWidth(max(150, min(int(cw), cap)))
    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        try:
            # v5.57 (redo, A9.2): "?" opens the shortcuts sheet —
            # but never while the user is typing in a text box
            # (this filter runs application-wide; text inputs keep
            # their "?"). Ctrl/Alt/Meta combinations are left alone.
            if event.type() == event.Type.KeyPress \
                    and event.key() == Qt.Key.Key_Question \
                    and not (event.modifiers()
                             & (Qt.KeyboardModifier.ControlModifier
                                | Qt.KeyboardModifier.AltModifier
                                | Qt.KeyboardModifier.MetaModifier)):
                from PySide6.QtWidgets import (
                    QApplication as _QApp,
                    QComboBox as _QComboBox,
                    QLineEdit as _QLineEdit,
                    QPlainTextEdit as _QPlainTextEdit,
                    QTextEdit as _QTextEdit,
                )
                _focus = _QApp.focusWidget()
                _typing = isinstance(
                    _focus, (_QLineEdit, _QPlainTextEdit, _QTextEdit))
                if isinstance(_focus, _QComboBox) and _focus.isEditable():
                    _typing = True
                if not _typing:
                    self._show_shortcuts_sheet()
                    return True
            # Chat UI (2026-11-16): Ctrl+D toggles dictation
            # (the mic's words stage in the composer as a draft),
            # and Esc cancels an edit or a reply staged in the
            # composer. Both only on the chat page, never while
            # the find bar owns the keys.
            if event.type() == event.Type.KeyPress:
                _mods = event.modifiers()
                _key = event.key()
                _on_chat = (getattr(self, "pages", None) is not None
                            and self.pages.currentIndex() == 0)
                _in_find = (getattr(self, "find_input", None) is not None
                            and obj is self.find_input)
                if (_on_chat and not _in_find
                        and _key == Qt.Key.Key_D
                        and _mods & Qt.KeyboardModifier.ControlModifier):
                    self._chat_toggle_dictation()
                    return True
                if (_on_chat and obj is getattr(self, "input_cmd", None)
                        and _key == Qt.Key.Key_Escape
                        and (getattr(self, "_editing_msg_id", None)
                             is not None
                             or getattr(self, "_quoting_msg_id", None)
                             is not None)):
                    self._cancel_composer_context()
                    return True
            if (hasattr(self, "chat_scroll")
                    and obj is self.chat_scroll.viewport()
                    and event.type() == event.Type.Resize):
                QTimer.singleShot(0, self._refit_chat_widths)
            # v5.31: the find bar's keyboard flow — Enter walks
            # forward, Shift+Enter back, Esc closes.
            if (getattr(self, "find_input", None) is not None
                    and obj is self.find_input
                    and event.type() == event.Type.KeyPress):
                key = event.key()
                if key == Qt.Key.Key_Escape:
                    self._close_chat_find()
                    return True
                if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    shift = bool(event.modifiers()
                                 & Qt.KeyboardModifier.ShiftModifier)
                    self._chat_find_step(-1 if shift else 1)
                    return True
        except Exception:  # noqa: BLE001
            pass
        return super().eventFilter(obj, event)
    def _jump_to_latest(self):
        bar = self.chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
        # UI rethink Build 1: snap the first visible message whole
        # (see _snap_chat_top_bubble) — one event-loop turn later,
        # so the scroll area has laid out.
        QTimer.singleShot(0, self._snap_chat_top_bubble)
        pill = getattr(self, "_btn_new_msgs", None)
        if pill is not None:
            pill.setVisible(False)
        # Chat UI (2026-11-16): the jump marks the first new
        # message with a hairline, clears the pill count, and
        # marks the newest message seen (the user is at the
        # bottom and has read up to here).
        self._mark_first_new_message()
        self._new_msgs_since = 0
        self._chat_first_new_msg_id = None
        try:
            self._chat_mark_last_seen()
        except Exception:  # noqa: BLE001
            pass
    def _snap_chat_top_bubble(self):
        """UI rethink Build 1: after a jump to latest, the first
        visible message is whole, never half-cut at the viewport's
        top edge — but only when the latest message stays whole
        too. The span from the first visible bubble's top to the
        last bubble's bottom can exceed the viewport (579 px of
        bubbles in a 525 px viewport on the 50-message thread),
        and then the two cannot both be whole: an unconditional
        snap trades a 59 px cut at the top for a 54 px cut at the
        bottom — the latest message, the one the jump exists to
        show. So the snap applies only when the last bubble stays
        fully visible at the snapped value; otherwise the scroll
        stays at the bottom (latest whole, top cut — the normal
        state of any scrolled list). Only the jump snaps — manual
        scrolling is never adjusted, and a bubble already whole
        (or none cut) is left exactly where it is."""
        try:
            bar = self.chat_scroll.verticalScrollBar()
            value = bar.value()
            bubbles = self._chat_bubbles()
            if not bubbles:
                return
            # Hunt #1 (2026-11-16): read layout slots, not live
            # geometry. A bubble mid-slide sits up to 8 px off its
            # slot, and a snap computed from the displaced position
            # leaves a bubble cut at the viewport top once the
            # slide finishes (flaky test_jump_to_latest: the last
            # bubble straddled the top). Re-validating the layout
            # first puts every bubble at its slot, so the snap
            # holds for the final state the bubbles settle into.
            try:
                layout = self.chat_layout
                layout.invalidate()
                layout.activate()
            except Exception:  # noqa: BLE001 — slots best effort
                pass
            last_bottom = bubbles[-1].geometry().bottom()
            viewport_h = self.chat_scroll.viewport().height()
            for bubble in bubbles:
                top = bubble.geometry().top()
                bottom = bubble.geometry().bottom()
                if top < value < bottom:
                    if last_bottom - top <= viewport_h:
                        bar.setValue(top)
                    return
        except Exception:  # noqa: BLE001 — cosmetic
            pass

    def _fade_in(self, widget: QWidget, animate: bool = True,
                 target_opacity: float = 1.0):
        """Wave 2 bubble entrance: fade + slight rise (~180 ms).

        Instant (no effect, full opacity, exact layout position)
        when animations are reduced, when ``animate`` is False, or
        while a bulk load is in progress (``_chat_bulk_loading`` —
        history restore must never animate). A watchdog snaps the
        widget to its final state if the animation never finishes.

        Chat UI (2026-11-16): ``target_opacity`` is the fade's
        end value — a dimmed (restored previous-day) bubble fades
        to the dimmed opacity, not full strength — and the finish
        re-installs the dim effect for a dimmed widget, so the
        dimming survives the entrance instead of being undone by
        the effect's removal.
        """
        if (not animate or getattr(self, "_chat_bulk_loading", False)
                or not self._animate_enabled()):
            try:
                widget.setGraphicsEffect(None)
            except Exception:  # noqa: BLE001
                pass
            if getattr(widget, "_chat_dimmed", False):
                try:
                    effect = QGraphicsOpacityEffect(widget)
                    effect.setOpacity(_DIMMED_OPACITY)
                    widget.setGraphicsEffect(effect)
                except Exception:  # noqa: BLE001
                    pass
            return
        try:
            effect = QGraphicsOpacityEffect(widget)
            effect.setOpacity(0.0)
            widget.setGraphicsEffect(effect)
            fade = QPropertyAnimation(effect, b"opacity", widget)
            fade.setDuration(DURATION_QUICK_MS)
            fade.setStartValue(0.0)
            fade.setEndValue(target_opacity)
            fade.setEasingCurve(EASE_PANEL)
            slide = _SlideOffset(widget, widget)
            # Hunt #1 (2026-11-16): the slide base must be the
            # layout-assigned position. A widget just added to a
            # layout still sits at (0, 0) until the event loop
            # runs the layout pass — a synchronous
            # invalidate()+activate() here does NOT position it
            # (verified by probe: pos stays (0, 0) until
            # processEvents), and neither does the rise
            # animation's first frame (it fires before the layout
            # pass). So the whole entrance starts on the next
            # event-loop turn, after the pending layout pass has
            # positioned the widget: the base is then captured
            # where the layout put it, and the bubble rises from
            # its own row instead of flashing in at the panel's
            # top. The widget is invisible until then (the effect
            # above already holds it at opacity 0.0), so the
            # one-turn delay shows nothing. The _finish heal
            # below is the backstop for any path where the base
            # was still captured pre-layout.
            rise = QPropertyAnimation(slide, b"offset", widget)
            rise.setDuration(DURATION_QUICK_MS)
            rise.setStartValue(8.0)
            rise.setEndValue(0.0)
            rise.setEasingCurve(EASE_PANEL)
            group = QParallelAnimationGroup(widget)
            group.addAnimation(fade)
            group.addAnimation(rise)

            def _finish(w=widget, s=slide):
                try:
                    s.snap()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    # Hunt #1 (2026-11-16): the slide base may
                    # have been captured before the layout
                    # positioned the widget, so snap() restores
                    # the pre-layout position and the bubble
                    # strands at the panel's top. Re-validating
                    # the parent layout after the snap puts every
                    # bubble where the layout says it belongs
                    # (verified by probe: invalidate()+activate()
                    # after the animation moves a stranded bubble
                    # to its correct row).
                    parent = w.parentWidget()
                    layout = (parent.layout()
                              if parent is not None else None)
                    if layout is not None:
                        layout.invalidate()
                        layout.activate()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    # Chat UI (2026-11-16): a dimmed widget keeps
                    # its dim effect — the fade's removal would
                    # otherwise restore it to full strength.
                    if getattr(w, "_chat_dimmed", False):
                        _eff = QGraphicsOpacityEffect(w)
                        _eff.setOpacity(_DIMMED_OPACITY)
                        w.setGraphicsEffect(_eff)
                    else:
                        w.setGraphicsEffect(None)
                except Exception:  # noqa: BLE001
                    pass

            group.finished.connect(_finish)
            # Watchdog: snap to final state even if never finished.
            QTimer.singleShot(400, _finish)
            widget._fade_anim = group  # keep a reference until it finishes
            widget._fade_slide = slide
            widget._fade_finish = _finish
            # Hunt #1: start on the next event-loop turn (see the
            # comment at the slide above) — the layout pass runs
            # first, so the slide base is the layout position.
            def _start(g=group):
                try:
                    g.start()
                except Exception:  # noqa: BLE001 — window gone
                    pass
            QTimer.singleShot(0, _start)
        except Exception:  # noqa: BLE001
            try:
                widget.setGraphicsEffect(None)
            except Exception:  # noqa: BLE001
                pass
    def _maybe_add_day_divider(self):
        """SENSES v5.16 (E17): a centred day divider before the first
        bubble of each calendar day in the live chat flow."""
        try:
            if getattr(self, "_chat_bulk_loading", False):
                return
            today = datetime.date.today()
            if getattr(self, "_last_chat_day", None) == today:
                return
            self._last_chat_day = today
            self._group_break = True
            lbl = QLabel(today.strftime("Today · %a %d %b"))
            lbl.setObjectName("chatDivider")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row = QHBoxLayout()
            # UI rethink Build 1: 14 px above a day divider — the
            # layout spacing is 0, so the row's top margin is the
            # gap (see _add_bubble).
            row.setContentsMargins(0, 14, 0, 0)
            row.addStretch()
            row.addWidget(lbl)
            row.addStretch()
            self.chat_layout.addLayout(row)
        except Exception:  # noqa: BLE001
            pass
    def _add_bubble(self, who: str, text: str, is_user: bool,
                    animate: bool = True, time_str: str | None = None,
                    image=None, caption=None, source=None,
                    msg_id: int | None = None, record: bool = True,
                    spoken: bool = False, dimmed: bool = False,
                    quote_id: int | None = None,
                    quote_text: str | None = None,
                    store_id: int | None = None):
        self._mark_chat_has_messages()
        self._maybe_add_day_divider()
        row = QHBoxLayout()
        bubble = ChatBubble(who, text, is_user, time_str=time_str,
                            image=image, caption=caption, source=source,
                            spoken=spoken)
        # Chat UI (2026-11-16): every bubble renders one node
        # of the transcript tree. The live path records a new
        # node (msg_id None); a branch switch re-renders an
        # existing node (msg_id given, record False) and the
        # bubble picks up that node's quote / pin / status from
        # the model instead of recording a duplicate. The tree
        # accessor lives on the sibling stream mixin, so it is
        # looked up — not called — here: a bare bubbles mixin
        # (the uia11y / uiinteraction harnesses exec the real
        # methods on a stub) has no tree, and the bubble still
        # renders with only the recording skipped.
        _thread_get = getattr(self, "_chat_thread", None)
        _thread = _thread_get() if _thread_get is not None else None
        if _thread is not None and record and msg_id is None:
            if is_user:
                _msg = _thread.add_user(
                    text, spoken=spoken, quote_id=quote_id,
                    quote_text=quote_text, ts=time_str,
                    store_id=store_id)
            else:
                _msg = _thread.add_assistant(text, ts=time_str,
                                             store_id=store_id)
            msg_id = _msg.id
        bubble._chat_msg_id = msg_id
        # Looked up, not assumed: the action dispatcher lives
        # on the sibling thread mixin, and a bare bubbles mixin
        # has none — the bubble's _emit_action already treats a
        # missing callback as "no window behaviour".
        bubble._on_chat_action = getattr(self, "_chat_bubble_action",
                                         None)
        if _thread is not None and msg_id is not None and not record:
            _node = _thread.get(msg_id)
            if _node is not None:
                bubble.set_pinned_state(_node.pinned)
                if _node.quote_text:
                    bubble.set_quote(_node.quote_text)
                if _node.status == "stopped":
                    bubble.set_status(
                        "stopped", "Stopped — kept what was said",
                        show_retry=True)
                elif _node.status == "failed":
                    bubble.set_status(
                        "failed",
                        _node.error or "That reply didn't arrive.",
                        show_retry=True)
                if _node.version_count > 1:
                    bubble.set_versions(
                        _node.version_count, _node.active,
                        self._chat_version_note(msg_id, _node.active))
        if dimmed:
            bubble.set_dimmed(True)
        self._fit_chat_item(bubble)
        # Phase 2 (chatbox #7): consecutive same-speaker bubbles
        # form a group — the name rides on the first, the time on
        # the last; anything else (card, system line, divider,
        # other speaker) breaks the run.
        prev = getattr(self, "_last_bubble", None)
        same_group = (
            prev is not None
            and getattr(self, "_last_bubble_user", None) == is_user
            and not getattr(self, "_group_break", True))
        # UI rethink Build 1: the transcript sets its own rhythm —
        # 6 px above a message, 2 px between grouped messages,
        # 14 px between speakers. The layout spacing is 0, so the
        # row's top margin IS the gap. This runs BEFORE the
        # assignments below overwrite _last_bubble_user — the
        # speaker-change check reads the PREVIOUS speaker.
        if same_group:
            row.setContentsMargins(0, 2, 0, 0)
        elif (prev is not None
              and getattr(self, "_last_bubble_user", None) != is_user):
            row.setContentsMargins(0, 14, 0, 0)
        else:
            row.setContentsMargins(0, 6, 0, 0)
        if same_group:
            bubble.set_group_head(False)
            prev.set_time_visible(False)
        self._last_bubble = bubble
        self._last_bubble_user = is_user
        self._group_break = False
        if is_user:
            row.addStretch()
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch()
            self._last_nova_bubble = bubble
        self.chat_layout.addLayout(row)
        # Conversion Wave 9: every live bubble also runs through
        # the v564 chat renderer (fallback path, lazy-optionals);
        # the on-screen bubble keeps its existing widgets.
        try:
            from voice_assistant.core.gui_facade import w9_wiring as _w9
            _w9.note_chat_message(self, who, text)
        except Exception:  # noqa: BLE001 — rendering never breaks chat
            pass
        # UI a11y foundations: Tab walks bubbles in arrival
        # order, through each bubble's actions, then to the
        # composer. The previous bubble's tail (or the static
        # chain anchor for the first bubble) points at the new
        # bubble; the new bubble's tail points at the composer.
        # This runs AFTER addLayout on purpose: setTabOrder
        # needs both widgets in the same window, and the bubble
        # only joins the window once its row is laid out.
        # Tab order never breaks chat, so this is guarded.
        try:
            from PySide6.QtWidgets import QWidget as _QW
            if prev is not None:
                _QW.setTabOrder(prev._tab_tail(), bubble)
            else:
                _anchor = getattr(self, "_chat_tab_anchor", None)
                if _anchor is not None:
                    _QW.setTabOrder(_anchor, bubble)
            # Chat UI (2026-11-16): the row's buttons chain in
            # row order (Copy, Regenerate/Edit, overflow), then
            # to the composer. Foot rows created later (status
            # Retry, version flip) re-chain through
            # _chain_bubble_tab (window_chat_stream.py), which
            # reads the live _tab_tail.
            _prev_w = bubble
            for _b in bubble._action_buttons:
                _QW.setTabOrder(_prev_w, _b)
                _prev_w = _b
            _cmd = getattr(self, "input_cmd", None)
            if _cmd is not None:
                _QW.setTabOrder(_prev_w, _cmd)
        except Exception:  # noqa: BLE001 — tab order never breaks chat
            pass
        # Chat UI (2026-11-16): a bubble that lands while the
        # user is reading back is a new message — count it for
        # the pill, and remember the first one so the jump can
        # mark the boundary. Bulk loads (restore, re-render)
        # never count.
        if (not getattr(self, "_chat_bulk_loading", False)
                and not getattr(self, "_chat_near_bottom", True)):
            self._new_msgs_since = getattr(self, "_new_msgs_since", 0) + 1
            if getattr(self, "_chat_first_new_msg_id", None) is None:
                self._chat_first_new_msg_id = msg_id
        # Chat UI (2026-11-16): a dimmed bubble fades to the
        # dimmed opacity, not full strength.
        self._fade_in(
            bubble, animate=animate,
            target_opacity=_DIMMED_OPACITY if dimmed else 1.0)
        self._scroll_chat_to_bottom()

    def _add_image_bubble(self, who: str, text: str, image,
                          caption=None, source=None,
                          animate: bool = True):
        """U6 (visual channel): a chat message carrying a diagram —
        the board's picture, or a figure from a supplied course
        file. The caption and source arrive gold-guarded
        (visual_channel.build_image_message); this method adds no
        guard of its own, because it must never see the gold."""
        self._add_bubble(who, text, False, animate=animate,
                         image=image, caption=caption, source=source)

    def _add_result_card(self, title: str, body: str,
                         tool_name: str | None = None,
                         answer: str | None = None,
                         msg_id: int | None = None,
                         record: bool = True):
        self._group_break = True
        self._mark_chat_has_messages()
        row = QHBoxLayout()
        card = QFrame()
        card.setObjectName("resultCard")
        card.setMaximumWidth(560)
        # Chat UI (2026-11-16): a card is a leaf on the transcript
        # tree like a bubble, so a branch switch re-renders it
        # instead of orphaning it. The live path records a new
        # node; a re-render passes the existing id with
        # record=False. Tree accessor looked up, not called —
        # see _add_bubble.
        _thread_get = getattr(self, "_chat_thread", None)
        _thread = _thread_get() if _thread_get is not None else None
        if _thread is not None and record and msg_id is None:
            _msg = _thread.add_card(title, body, tool_name=tool_name,
                                    answer=answer)
            msg_id = _msg.id
        card._chat_msg_id = msg_id
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(14, 12, 14, 12)
        c_layout.setSpacing(2)
        # Refinement A: a small category icon beside the title, mapped
        # from the tool name (icons.py names only; silently absent if
        # the icon can't be rendered — never a broken glyph).
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        try:
            pix = make_icon(
                _tool_icon_name(tool_name or title), ACCENT_2, 14
            ).pixmap(QSize(14, 14))
            icon_lbl = QLabel()
            icon_lbl.setPixmap(pix)
            title_row.addWidget(icon_lbl)
        except Exception:  # noqa: BLE001
            pass
        t = QLabel(_title_case(title))
        t.setObjectName("resultTitle")
        if tool_name:
            # The raw tool name stays available — as a tooltip, not a title.
            t.setToolTip(tool_name)
        title_row.addWidget(t)
        title_row.addStretch()
        # Chat UI (2026-11-16): the answer first — one line, in
        # her voice, at the top of the card, before the detail.
        # The caller phrases it ("Done — …"); a card without one
        # renders exactly as before.
        if answer:
            a = QLabel()
            a.setObjectName("resultBody")
            a.setWordWrap(True)
            a.setTextFormat(Qt.TextFormat.RichText)
            a.setText('<div style="line-height: 140%"><b>'
                      + _md_lite(answer) + "</b></div>")
            a.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextBrowserInteraction)
            c_layout.addLayout(title_row)
            c_layout.addWidget(a)
        b = QLabel()
        b.setObjectName("resultBody")
        b.setWordWrap(True)
        # Phase 2 (chatbox #15): card bodies get the same
        # markdown-lite rendering as bubbles.
        b.setTextFormat(Qt.TextFormat.RichText)
        b.setText('<div style="line-height: 140%">'
                  + _md_lite(body) + "</div>")
        b.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        b.setOpenExternalLinks(True)
        if not answer:
            c_layout.addLayout(title_row)
        c_layout.addWidget(b)
        shown = body
        toggle = None
        if len(body) > 280:
            # SENSES v5.16 (E15): long results start collapsed.
            # Phase 2 (chatbox #15): cut at a word boundary, never
            # mid-word.
            _cut = body[:240]
            if " " in _cut:
                _cut = _cut[:_cut.rfind(" ")]
            shown = _cut.rstrip() + "…"
            b.setText('<div style="line-height: 140%">'
                      + _md_lite(shown) + "</div>")
            toggle = QPushButton("Show more")
            toggle.setStyleSheet(
                f"QPushButton {{ color: {ACCENT_2}; background: transparent;"
                " border: none; text-align: left; padding: 2px 0; }")
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            state = {"open": False}

            def _flip(_=False, _b=b, _card=card, _t=None):
                state["open"] = not state["open"]
                _b.setText('<div style="line-height: 140%">'
                           + _md_lite(body if state["open"] else shown)
                           + "</div>")
                if _t is not None:
                    _t.setText("Show less" if state["open"] else "Show more")
                w = QFontMetrics(_b.font()).horizontalAdvance(_b.text())
                _card.setProperty("content_w", max(170, min(560, w + 30)))
                self._fit_chat_item(_card)

            toggle.clicked.connect(lambda _=False: _flip(_t=toggle))
            c_layout.addWidget(toggle)
        body_w = QFontMetrics(b.font()).horizontalAdvance(shown)
        card.setProperty("content_w", max(170, min(560, body_w + 30)))
        self._fit_chat_item(card)
        # UI rethink Build 1: a card is a message-like item — 6 px
        # above it, the same as a new message (see _add_bubble).
        row.setContentsMargins(0, 6, 0, 0)
        row.addWidget(card)
        row.addStretch()
        self.chat_layout.addLayout(row)
        self._fade_in(card)
        self._scroll_chat_to_bottom()
    def _add_system(self, text: str, msg_id: int | None = None,
                    record: bool = True):
        self._group_break = True
        # Chat UI (2026-11-16): a system line is a leaf on the
        # transcript tree like a bubble, so a branch switch
        # re-renders it instead of orphaning it. Tree accessor
        # looked up, not called — see _add_bubble.
        _thread_get = getattr(self, "_chat_thread", None)
        _thread = _thread_get() if _thread_get is not None else None
        if _thread is not None and record and msg_id is None:
            _msg = _thread.add_system(text)
            msg_id = _msg.id
        lbl = QLabel(text)
        lbl.setObjectName("systemLine")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setWordWrap(True)
        lbl._chat_msg_id = msg_id
        # UI rethink Build 1: a system line is a message-like item
        # — 6 px above it. It rides in a row (like the day divider)
        # because a bare label's contents margins do not move its
        # layout height; the row's top margin is the gap.
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        row.addWidget(lbl)
        self.chat_layout.addLayout(row)
        self._scroll_chat_to_bottom()
    def _notify(self, text: str):
        """Friendly inline notice — replaces popup dialogs."""
        self._show_page(0)
        self._add_system(text)
