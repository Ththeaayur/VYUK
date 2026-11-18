"""MainWindow chat mixin: the transcript as a tree (chat UI build,
2026-11-16).

Moves 4–12 and 14: edit-and-branch, spoken words landing as
text + dictation, the composer (draft, queue), quote and reply,
find that finds, history position + the conversation list, the
new-messages pill with a count, message actions (pin / delete /
copy-as-plain-text behind one overflow), cards that answer first,
and the empty chat that teaches.

The transcript tree (``core.chat_thread``) is the source of
truth for what is on screen: every bubble, card, and system line
renders one node, and a branch switch re-renders the visible
path instead of patching widgets. Branches are session-local —
the store keeps the flat log; the tree lives for this session.
"""

from __future__ import annotations

import asyncio
import datetime
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from voice_assistant.core.gui_facade import live_session
from voice_assistant.core.gui_facade import memory_store
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set

from .window_chat_conversations import ConversationsDialog

# The composer queue (move 6): how many typed messages may wait
# while she is speaking. Three is enough to hold a thought
# without letting a queue grow into a second conversation.
_CHAT_QUEUE_CAP = 3


class ChatThreadMixin:
    # ------------------------------------------------------------ echo sets
    def _suppress_user_echo(self, text: str):
        """Mark a resend's text so its transcript echo adds no
        second user bubble: the turn is already on screen. One
        marker is consumed by one echo; markers expire after
        20 s (the _self_sent_texts shape)."""
        try:
            pending = getattr(self, "_chat_suppress_echo", None)
            if pending is None:
                pending = self._chat_suppress_echo = []
            now = time.monotonic()
            pending[:] = [(t, exp) for (t, exp) in pending
                          if exp > now]
            pending.append((text, now + 20.0))
        except Exception:  # noqa: BLE001
            pass

    def _consume_suppressed_echo(self, text: str) -> bool:
        try:
            pending = getattr(self, "_chat_suppress_echo", None)
            if not pending:
                return False
            now = time.monotonic()
            for i, (t, exp) in enumerate(pending):
                if exp > now and t == text:
                    del pending[i]
                    return True
            pending[:] = [(t, exp) for (t, exp) in pending
                          if exp > now]
            return False
        except Exception:  # noqa: BLE001
            return False

    def _note_typed_echo(self, text: str):
        """A typed submit's text, so its echo is recognised as
        typed (not spoken) when it lands in _on_transcript."""
        try:
            pending = getattr(self, "_chat_typed_echo", None)
            if pending is None:
                pending = self._chat_typed_echo = []
            now = time.monotonic()
            pending[:] = [(t, exp) for (t, exp) in pending
                          if exp > now]
            pending.append((text, now + 20.0))
        except Exception:  # noqa: BLE001
            pass

    def _consume_typed_echo(self, text: str) -> bool:
        try:
            pending = getattr(self, "_chat_typed_echo", None)
            if not pending:
                return False
            now = time.monotonic()
            for i, (t, exp) in enumerate(pending):
                if exp > now and t == text:
                    del pending[i]
                    return True
            pending[:] = [(t, exp) for (t, exp) in pending
                          if exp > now]
            return False
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ actions
    def _chat_bubble_action(self, kind: str, bubble):
        """Every bubble action reports here (kind, bubble). The
        bubble never owns behaviour — Copy and Copy-as-plain-text
        stay local to the bubble; everything else is the window's
        and goes through the tree. A bubble without a tree id
        (a history-window bubble whose node lives in a temporary
        model — see _chat_load_history_window) still answers
        Copy; the rest quietly do nothing."""
        try:
            msg_id = getattr(bubble, "_chat_msg_id", None)
            if kind == "regenerate":
                if msg_id is not None:
                    self._regenerate_message(msg_id)
            elif kind == "retry":
                if msg_id is not None:
                    self._retry_message(msg_id)
            elif kind == "edit":
                if msg_id is not None:
                    self._edit_message(msg_id)
            elif kind in ("pin", "unpin"):
                if msg_id is not None:
                    self._toggle_pin(msg_id, kind == "pin")
            elif kind == "delete":
                if msg_id is not None:
                    self._delete_message(msg_id)
            elif kind == "quote":
                if msg_id is not None:
                    self._quote_message(msg_id)
            elif kind == "quote_jump":
                if msg_id is not None:
                    self._jump_to_quote(msg_id)
            elif kind == "flip_prev":
                if msg_id is not None:
                    self._flip_version(msg_id, -1)
            elif kind == "flip_next":
                if msg_id is not None:
                    self._flip_version(msg_id, 1)
        except Exception:  # noqa: BLE001 — an action never breaks chat
            pass

    def _flip_version(self, msg_id: int, delta: int):
        """Flip one message between its versions ("1 of 2").
        The flip re-renders the visible thread from the model —
        the children that answered the old version return, the
        ones that answered the new one hide — instead of
        swapping one bubble's text and leaving its branch
        behind."""
        try:
            thread = self._chat_thread()
            node = thread.get(msg_id)
            if node is None or node.version_count <= 1:
                return
            new = node.active + delta
            if not 0 <= new < node.version_count:
                return
            thread.set_active_version(msg_id, new)
            self._render_visible_thread(scroll_to_msg_id=msg_id)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ edit
    def _edit_message(self, msg_id: int):
        """Edit a sent message in the composer: the text loads,
        Enter saves, Esc cancels. Saving replaces the message
        in place and forks the conversation from that point —
        the old branch stays reachable through the same flip
        control as regenerate."""
        try:
            thread = self._chat_thread()
            node = thread.get(msg_id)
            if node is None or node.role != "user":
                return
            self._editing_msg_id = msg_id
            self._quoting_msg_id = None
            self.input_cmd.setText(node.text)
            self.input_cmd.setFocus()
            self._show_composer_context(
                "Editing your message — Enter to save, Esc to cancel")
            self._sync_send_button()
        except Exception:  # noqa: BLE001
            pass

    def _commit_edit(self):
        """Save the edit: a new version of the user message, the
        thread re-rendered from that point (the old answer is
        gone from view, one flip away), and the edited text sent
        as a new turn for its new answer."""
        try:
            msg_id = getattr(self, "_editing_msg_id", None)
            if msg_id is None:
                return False
            text = (self.input_cmd.text() or "").strip()
            if not text:
                # An empty edit is a cancelled edit.
                self._cancel_composer_context()
                return True
            thread = self._chat_thread()
            node = thread.get(msg_id)
            if node is None:
                self._cancel_composer_context()
                return True
            if text == node.text:
                # Nothing changed — no fork, no resend.
                self._cancel_composer_context()
                return True
            thread.edit_user(msg_id, text)
            self._note_chat_version(
                msg_id, node.active, "Edited — new answer")
            self._render_visible_thread(scroll_to_msg_id=msg_id)
            self._suppress_user_echo(text)
            self._note_self_sent(text)
            if self.worker is not None and self.worker.loop is not None \
                    and live_session.is_connected:
                _fut = asyncio.run_coroutine_threadsafe(
                    live_session.send_text(text), self.worker.loop)
                self._typed_send_future = _fut
                self._chat_turn_in_flight = True
                # Field-check fix #7 (U4 #29/#30): an edit's
                # resend is a send like any other — tag its
                # generation, and tell the done callback it was
                # a typed send, not a regenerate resend.
                self._chat_note_send(text)
                _fut.add_done_callback(
                    lambda f: self._on_typed_send_done(f, kind="typed"))
                self._show_thinking_bubble()
            # The edit is committed: only now does the composer
            # clear (a failed commit above leaves the text in
            # place, so Enter can try again instead of losing it).
            self._cancel_composer_context()
            return True
        except Exception:  # noqa: BLE001 — an edit never breaks chat
            return False

    # ------------------------------------------------------------ quote
    def _quote_message(self, msg_id: int):
        """Reply to one message: the composer stages the quote
        and the send carries it (the bubble shows the quote
        anchor above its body)."""
        try:
            thread = self._chat_thread()
            node = thread.get(msg_id)
            if node is None:
                return
            self._quoting_msg_id = msg_id
            self._editing_msg_id = None
            first = (node.text or "").strip().split("\n")[0]
            if len(first) > 50:
                first = first[:47] + "…"
            self._show_composer_context(f"Replying to “{first}”")
            self.input_cmd.setFocus()
        except Exception:  # noqa: BLE001
            pass

    def _jump_to_quote(self, msg_id: int):
        """Jump back to the message a bubble quotes: steer the
        visible thread onto the quoted message's branch, render
        it, and glow the quoted bubble."""
        try:
            thread = self._chat_thread()
            node = thread.get(msg_id)
            if node is None or node.quote_id is None:
                return
            if not thread.make_visible(node.quote_id):
                return
            self._render_visible_thread(
                scroll_to_msg_id=node.quote_id)
            bubble = self._chat_bubble_for(node.quote_id)
            if bubble is not None:
                self._glow_bubble(bubble)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ pin / delete
    def _toggle_pin(self, msg_id: int, pinned: bool):
        try:
            thread = self._chat_thread()
            if not thread.set_pinned(msg_id, pinned):
                return
            bubble = self._chat_bubble_for(msg_id)
            if bubble is not None:
                bubble.set_pinned_state(pinned)
            self._refresh_chat_header()
        except Exception:  # noqa: BLE001
            pass

    def _delete_message(self, msg_id: int):
        """Delete one message: it (and the branch under it)
        leaves the visible thread, and an inline undo row
        stands where it was for a few seconds. The node stays
        in the tree, so undo is a flag clear, not a
        reconstruction."""
        try:
            thread = self._chat_thread()
            node = thread.get(msg_id)
            if node is None:
                return
            bubble = self._chat_bubble_for(msg_id)
            row_index = self._chat_row_index_for(bubble) \
                if bubble is not None else None
            thread.delete_message(msg_id)
            self._render_visible_thread()
            if row_index is not None:
                self._show_deleted_undo(msg_id, row_index)
            self._refresh_chat_header()
        except Exception:  # noqa: BLE001
            pass

    def _show_deleted_undo(self, msg_id: int, row_index: int):
        """The undo row where the message was: "Message deleted"
        with an Undo button. It removes itself after 10 s or
        when the next message arrives — whichever comes first."""
        try:
            old = getattr(self, "_chat_undo_row", None)
            if old is not None:
                self.chat_layout.removeWidget(old)
                old.deleteLater()
                self._chat_undo_row = None
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 2, 0, 2)
            layout.setSpacing(8)
            lbl = QLabel("Message deleted")
            lbl.setObjectName("muted")
            layout.addWidget(lbl)
            undo = QPushButton("Undo")
            undo.setObjectName("chipBtn")
            undo.setAccessibleName("Undo delete")
            undo.clicked.connect(
                lambda _=False, _id=msg_id: self._undo_delete(_id))
            layout.addWidget(undo)
            layout.addStretch()
            self.chat_layout.insertWidget(
                max(1, min(row_index, self.chat_layout.count())),
                row)
            self._chat_undo_row = row
            QTimer.singleShot(
                10000, lambda: self._clear_deleted_undo(row))
        except Exception:  # noqa: BLE001
            pass

    def _clear_deleted_undo(self, row=None):
        try:
            target = row if row is not None else getattr(
                self, "_chat_undo_row", None)
            if target is None:
                return
            self.chat_layout.removeWidget(target)
            target.deleteLater()
            if getattr(self, "_chat_undo_row", None) is target:
                self._chat_undo_row = None
        except Exception:  # noqa: BLE001
            pass

    def _undo_delete(self, msg_id: int):
        try:
            self._clear_deleted_undo()
            self._chat_thread().restore_message(msg_id)
            self._render_visible_thread(scroll_to_msg_id=msg_id)
            self._refresh_chat_header()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ render
    def _chat_row_index_for(self, bubble):
        """The chat-layout index of the row holding one bubble
        (for inserting the undo row where the message was)."""
        try:
            for i in range(self.chat_layout.count()):
                item = self.chat_layout.itemAt(i)
                if item is None:
                    continue
                sub = item.layout()
                if sub is None:
                    continue
                for j in range(sub.count()):
                    if sub.itemAt(j).widget() is bubble:
                        return i
        except Exception:  # noqa: BLE001
            pass
        return None

    def _render_visible_thread(self, scroll_to_msg_id=None):
        """Re-render the visible thread from the tree: clear the
        view (not the model), then re-add the path's nodes in
        order — bubbles, cards, system lines — with day
        dividers where the day changes. Bulk-loaded, never
        animated. ``scroll_to_msg_id`` parks the view on one
        message (a flip, a quote jump); otherwise it settles
        at the bottom."""
        try:
            thread = self._chat_thread()
            items = thread.visible_thread()
            self._chat_bulk_loading = True
            try:
                self._clear_chat_view_only()
                last_day = None
                today = datetime.date.today()
                for node in items:
                    day = self._chat_node_day(node)
                    if day is not None and day != last_day:
                        last_day = day
                        if day == today:
                            label = "Earlier today"
                        elif day == today - datetime.timedelta(days=1):
                            label = "Yesterday"
                        else:
                            label = day.strftime("%d %b")
                        self._add_system(label, msg_id=None,
                                         record=False)
                    if node.kind == "text":
                        when = self._chat_node_time(node)
                        self._add_bubble(
                            "You" if node.role == "user"
                            else self._assistant_display_name(),
                            node.text, node.role == "user",
                            animate=False, time_str=when,
                            msg_id=node.id, record=False,
                            spoken=node.spoken,
                            dimmed=self._chat_node_dimmed(node),
                            quote_id=node.quote_id,
                            quote_text=node.quote_text)
                    elif node.kind == "card":
                        self._add_result_card(
                            node.card_title or "", node.text,
                            tool_name=node.card_tool,
                            answer=node.card_answer,
                            msg_id=node.id, record=False)
                    elif node.kind == "system":
                        self._add_system(node.text, msg_id=node.id,
                                         record=False)
                if last_day is not None:
                    self._last_chat_day = last_day
            finally:
                self._chat_bulk_loading = False
            self._refresh_chat_header()
            if scroll_to_msg_id is not None:
                bubble = self._chat_bubble_for(scroll_to_msg_id)
                if bubble is not None:
                    self.chat_scroll.ensureWidgetVisible(bubble, 0, 40)
                    return
            self._scroll_chat_to_bottom()
        except Exception:  # noqa: BLE001 — rendering never breaks chat
            pass

    def _assistant_display_name(self):
        try:
            from voice_assistant.core.gui_facade import config
            return config.assistant_name
        except Exception:  # noqa: BLE001
            return "VYUK"

    @staticmethod
    def _chat_node_day(node):
        """The local calendar date of a tree node, from its
        stored timestamp — None when the node carries no date
        (a live message's HH:MM, or a system line)."""
        raw = (getattr(node, "ts", None) or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone().date()

    @staticmethod
    def _chat_node_time(node):
        raw = (getattr(node, "ts", None) or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return raw  # an HH:MM time_str passes through as-is
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone().strftime("%H:%M")

    @staticmethod
    def _chat_node_dimmed(node):
        """A restored previous-day message renders slightly
        muted; a live message never does."""
        day = ChatThreadMixin._chat_node_day(node)
        return day is not None and day != datetime.date.today()

    def _clear_chat_view_only(self):
        """The view-only clear (the existing _clear_chat_view
        body, without touching the tree): every bubble, card,
        and system line leaves the layout; the leading stretch
        stays. Used by _render_visible_thread."""
        # Round-2 N6: clear any find glow FIRST, while the
        # glowing bubble still lives — this clear deletes it,
        # and the 1200 ms glow timer would otherwise fire on
        # the deleted object. This also nulls the ref, so a
        # pending timer becomes a no-op. (_clear_find_glow
        # itself is also guarded against a dead bubble, so a
        # clear from any other path cannot raise either.)
        self._clear_find_glow()
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                self._delete_layout_items(item.layout())
        self._chat_has_messages = False
        self._last_nova_bubble = None
        self._last_bubble = None
        self._last_bubble_user = None
        self._group_break = True
        self._last_chat_day = None
        self._thinking_row = None
        self._thinking_bubble = None
        for attr in ("_suggest_row", "_quick_row"):
            row = getattr(self, attr, None)
            if row is not None:
                try:
                    row.setVisible(True)
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------ composer
    def _show_composer_context(self, text: str):
        """The one line above the composer that says what the
        composer is doing right now — editing a message, or
        replying to one — with a × to cancel. One row serves
        both states; the voice-state line hides while it shows."""
        try:
            row = getattr(self, "_composer_context_row", None)
            lbl = getattr(self, "_composer_context_lbl", None)
            if row is None or lbl is None:
                return
            lbl.setText(text)
            row.setVisible(True)
            voice = getattr(self, "_voice_state_lbl", None)
            if voice is not None:
                voice.setVisible(False)
        except Exception:  # noqa: BLE001
            pass

    def _cancel_composer_context(self):
        """Cancel an edit or a reply: the composer empties, the
        context row hides, the voice line comes back."""
        try:
            self._editing_msg_id = None
            self._quoting_msg_id = None
            self.input_cmd.clear()
            row = getattr(self, "_composer_context_row", None)
            if row is not None:
                row.setVisible(False)
            self._chat_voice_state_update(
                getattr(self, "_status_raw", ""))
            self._sync_send_button()
        except Exception:  # noqa: BLE001
            pass

    def _chat_is_mid_reply(self) -> bool:
        """True while she is mid-reply — a live reply is
        streaming, or the status says SPEAKING. A typed send
        in that window queues instead of going live."""
        live = getattr(self, "_live_reply", None)
        if live is not None and not live.get("finalized"):
            return True
        return (getattr(self, "_status_raw", "") or "") == "SPEAKING"

    def _chat_queue_typed(self, text: str) -> bool:
        """Queue a typed message while she speaks: it shows at
        once as the user's bubble, marked Queued, and sends
        itself (FIFO) when the turn ends. Returns False when
        the queue is full — the draft stays in the composer
        and the user is told why."""
        try:
            queue = getattr(self, "_chat_queue", None)
            if queue is None:
                queue = self._chat_queue = []
            if len(queue) >= _CHAT_QUEUE_CAP:
                self._add_system(
                    "The queue is full — your message is still "
                    "in the composer. It will send when she's done.")
                return False
            queue.append(text)
            self._add_bubble_queued(text)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _add_bubble_queued(self, text: str):
        """The queued message's bubble: the user's words, marked
        Queued, recorded in the tree with status queued."""
        try:
            before = len(self._chat_bubbles())
            self._add_bubble("You", text, is_user=True, animate=True)
            bubbles = self._chat_bubbles()
            if len(bubbles) <= before:
                return None
            bubble = bubbles[-1]
            bubble.set_status(
                "queued", "Queued — will send when she's done",
                show_retry=False)
            msg_id = getattr(bubble, "_chat_msg_id", None)
            if msg_id is not None:
                self._chat_thread().set_status(msg_id, "queued")
            self._chain_bubble_tab(bubble)
            return bubble
        except Exception:  # noqa: BLE001
            return None

    def _chat_queue_flush(self):
        """Send the queued messages, FIFO, now that the turn is
        over. Each sends through the normal typed path with its
        echo suppressed (its bubble is already on screen) and
        its Queued line cleared."""
        try:
            queue = getattr(self, "_chat_queue", None)
            if not queue:
                return
            if self._chat_is_mid_reply():
                return
            # One send per turn: a flush that lands while a turn
            # is already in flight (the thinking bubble is up
            # for the message the last flush sent) waits for that
            # turn to land — otherwise the turn-landed flush and
            # the LISTENING flush would send two queued messages
            # for one turn end.
            if getattr(self, "_chat_turn_in_flight", False):
                return
            if not (self.worker is not None
                    and self.worker.loop is not None
                    and live_session.is_connected):
                return
            text = queue.pop(0)
            bubble = None
            for b in self._chat_bubbles():
                if getattr(b, "_chat_queued_text", None) == text:
                    bubble = b
                    break
            self._suppress_user_echo(text)
            self._note_self_sent(text)
            _fut = asyncio.run_coroutine_threadsafe(
                live_session.send_text(text), self.worker.loop)
            self._typed_send_future = _fut
            self._chat_turn_in_flight = True
            # Field-check fix #7 (U4 #29/#30): a queued flush
            # is a typed send — tag its generation, and tell
            # the done callback so.
            self._chat_note_send(text)
            _fut.add_done_callback(
                lambda f: self._on_typed_send_done(f, kind="typed"))
            if bubble is not None:
                bubble._chat_queued_text = None
                bubble.set_status("", "", show_retry=False)
                msg_id = getattr(bubble, "_chat_msg_id", None)
                if msg_id is not None:
                    self._chat_thread().set_status(msg_id, "done")
            self._show_thinking_bubble()
            # A second queued message waits for the next turn
            # end — one send per turn, so the answers stay in
            # the order the questions were asked.
        except Exception:  # noqa: BLE001 — the queue never breaks chat
            pass

    # ------------------------------------------------------------ dictation
    # Field-check fix #6 (U3 #15): the whole utterances that
    # command Dictation itself — "stop dictating" turns it off
    # instead of staging into the composer as draft text.
    _STOP_DICTATION_COMMANDS = frozenset({
        "stop dictating", "stop dictation"})

    def _chat_stop_dictation_command(self, text: str) -> bool:
        """A spoken "stop dictating" while Dictation is on turns
        it off instead of staging into the composer as draft
        text. Whole utterance only (after strip/lower/trailing-
        punctuation strip) — "stop dictating the letter" is
        dictation, not a command. Returns True when it consumed
        the utterance."""
        try:
            if not getattr(self, "_dictation_mode", False):
                return False
            normalised = (text or "").strip().lower()
            while normalised and normalised[-1] in ".,!?;:":
                normalised = normalised[:-1].rstrip()
            if normalised not in self._STOP_DICTATION_COMMANDS:
                return False
            self._chat_toggle_dictation()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _chat_toggle_dictation(self):
        """Dictation on / off. While on, the mic's words stage
        in the composer as a draft for review — nothing is ever
        sent unreviewed — and the voice line reads Dictating.
        The button and the voice line both follow the flag.

        Field-check fix #6 (U3 #14): turning on with no live
        session refuses instead of faking "Dictating…" — nothing
        is listening, so the flag stays off, the button stays
        "Dictate", and a system line says why, same shape as the
        typed path's "You're not connected yet" notice."""
        try:
            on = not bool(getattr(self, "_dictation_mode", False))
            if on and not live_session.is_connected:
                self._add_system(
                    "Connect first — dictation needs the mic "
                    "running.")
                return
            self._dictation_mode = on
            btn = getattr(self, "_btn_dictate", None)
            if btn is not None:
                btn.setChecked(on)
                btn.setText("Dictating…" if on else "Dictate")
            self._chat_voice_state_update(
                getattr(self, "_status_raw", ""))
            if on:
                self.input_cmd.setFocus()
        except Exception:  # noqa: BLE001
            pass

    def _chat_dictation_stage(self, text: str) -> bool:
        """Stage dictated words in the composer as a draft.
        Returns True when the words were staged (the caller
        then skips the bubble entirely)."""
        try:
            if not getattr(self, "_dictation_mode", False):
                return False
            current = (self.input_cmd.text() or "").strip()
            staged = f"{current} {text}".strip() if current else text
            self.input_cmd.setText(staged)
            self._chat_save_composer_draft()
            return True
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ draft
    def _chat_schedule_draft_save(self):
        """Restart the 1 s draft-save debounce (wired to the
        composer's textChanged)."""
        try:
            timer = getattr(self, "_chat_draft_timer", None)
            if timer is not None:
                timer.start()
        except Exception:  # noqa: BLE001
            pass

    def _chat_save_composer_draft(self):
        """Persist the composer's draft (debounced by the
        caller — a QTimer, not per keystroke). An empty draft
        clears the stored one, so a sent message leaves no
        stale text behind."""
        try:
            text = self.input_cmd.text() if hasattr(
                self, "input_cmd") else ""
            qsettings_set("chat/composer_draft", text or "",
                          settings=self.settings)
        except Exception:  # noqa: BLE001
            pass

    def _chat_restore_composer_draft(self):
        """Restore the draft when the chat page is shown — the
        half-typed message is still there after a page switch
        or a restart."""
        try:
            draft = qsettings_get("chat/composer_draft",
                                  settings=self.settings) or ""
            if draft and not (self.input_cmd.text() or "").strip():
                self.input_cmd.setText(draft)
                self._sync_send_button()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ find / history
    def _chat_open_history_window(self, rows, match_id=None):
        """Show a stored window (a find match's surroundings, or
        one conversation day) in place of the live thread. The
        live tree is saved first and restored by Back to latest
        — branches are never lost to a history read. The window
        is a linear thread built from the stored rows; its
        bubbles carry the window's own node ids, so Copy works
        and the rest of the actions work inside the window."""
        try:
            from voice_assistant.core.gui_facade import chat_thread as _ct
            if getattr(self, "_chat_viewing_history", False):
                pass  # already in a window: swap it, keep the saved live tree
            else:
                self._chat_saved_thread = self._chat_thread()
                self._chat_viewing_history = True
            window_thread = _ct.ChatThread()
            self._chat_thread_obj = window_thread
            self._chat_bulk_loading = True
            try:
                self._clear_chat_view_only()
                last_day = None
                today = datetime.date.today()
                for entry in rows or []:
                    role = str(entry.get("role") or "")
                    content = str(entry.get("content") or "")
                    if not content:
                        continue
                    day = self._history_entry_date(entry)
                    if day is not None and day != last_day:
                        last_day = day
                        if day == today:
                            label = "Earlier today"
                        elif day == today - datetime.timedelta(days=1):
                            label = "Yesterday"
                        else:
                            label = day.strftime("%d %b")
                        self._add_system(label, msg_id=None,
                                         record=False)
                    when = self._history_entry_time(entry)
                    if role == "user":
                        self._add_bubble(
                            "You", content, is_user=True,
                            animate=False, time_str=when,
                            dimmed=day is not None and day != today,
                            store_id=entry.get("id"))
                    elif role == "assistant":
                        self._add_bubble(
                            self._assistant_display_name(), content,
                            is_user=False, animate=False, time_str=when,
                            dimmed=day is not None and day != today,
                            store_id=entry.get("id"))
                if last_day is not None:
                    self._last_chat_day = last_day
            finally:
                self._chat_bulk_loading = False
            self._refresh_chat_header()
            if match_id is not None:
                bubble = None
                for b in self._chat_bubbles():
                    if getattr(b, "_chat_msg_id", None) is not None:
                        node = window_thread.get(b._chat_msg_id)
                        if node is not None and getattr(
                                node, "store_id", None) == match_id:
                            bubble = b
                            break
                if bubble is not None:
                    self.chat_scroll.ensureWidgetVisible(bubble, 0, 40)
                    self._glow_bubble(bubble)
                    return
            self._scroll_chat_to_bottom()
        except Exception:  # noqa: BLE001 — history never breaks chat
            pass

    def _chat_back_to_latest(self):
        """Leave a history window: the saved live tree comes
        back and renders. When there is no saved tree (the
        window was opened before any live tree existed), the
        latest 30 stored entries render instead."""
        try:
            saved = getattr(self, "_chat_saved_thread", None)
            viewing = bool(
                getattr(self, "_chat_viewing_history", False))
            self._chat_viewing_history = False
            self._chat_saved_thread = None
            if saved is not None:
                if viewing:
                    # Field-check fix #7 (U4 #28): a regenerate
                    # / retry inside the window grew versions on
                    # the window thread — merge them into the
                    # saved live tree before it comes back, or
                    # the new branch dies with the window.
                    self._chat_merge_history_versions(
                        self._chat_thread(), saved)
                self._chat_thread_obj = saved
                self._render_visible_thread()
            else:
                self._restore_chat_history()
            self._refresh_chat_header()
        except Exception:  # noqa: BLE001
            pass

    def _chat_merge_history_versions(self, window_thread, live_thread):
        """Merge versions grown inside a history window back
        into the saved live tree (U4 #28). A regenerate / retry
        in the window adds a version to the window thread's
        node; Back to latest used to drop the window thread
        whole, discarding the new branch. For each versioned
        text node on the window's visible thread, find the
        same message in the live tree — by store id when both
        sides carry one, else by the question it answers plus
        its original text — and copy the versions it lacks,
        keeping the window's active version active (with its
        version note, when one was recorded). Nodes with no
        counterpart are left out: a wholly new branch grown
        in the window (e.g. the new answer to an edited
        question) has no live node to merge into, so it stays
        with the window."""
        try:
            if window_thread is None or live_thread is None:
                return
            if window_thread is live_thread:
                return
            live_nodes = list(live_thread.visible_thread())
            notes = getattr(self, "_chat_version_notes", None) or {}
            for wnode in window_thread.visible_thread():
                if wnode.kind != "text" or wnode.version_count <= 1:
                    continue
                target = None
                if getattr(wnode, "store_id", None) is not None:
                    for cand in live_nodes:
                        if getattr(cand, "store_id", None) \
                                == wnode.store_id:
                            target = cand
                            break
                if target is None:
                    # Content match: same role, same original
                    # text, answering the same user text.
                    wparent = window_thread.get(wnode.parent_id) \
                        if wnode.parent_id is not None else None
                    wq = wparent.text if wparent is not None else ""
                    for cand in live_nodes:
                        if cand.role != wnode.role:
                            continue
                        if not cand.versions or \
                                cand.versions[0] != wnode.versions[0]:
                            continue
                        cparent = live_thread.get(cand.parent_id) \
                            if cand.parent_id is not None else None
                        cq = cparent.text \
                            if cparent is not None else ""
                        if cq == wq:
                            target = cand
                            break
                if target is None:
                    continue
                for idx, text in enumerate(wnode.versions):
                    if idx == 0:
                        continue
                    if text in target.versions:
                        continue
                    if live_thread.add_version(target.id, text):
                        new_idx = target.version_count - 1
                        note = notes.get((wnode.id, idx))
                        if note:
                            self._note_chat_version(
                                target.id, new_idx, note)
                active_text = wnode.versions[wnode.active]
                if active_text in target.versions:
                    live_thread.set_active_version(
                        target.id,
                        target.versions.index(active_text))
        except Exception:  # noqa: BLE001 — a merge never breaks Back
            pass

    def _chat_open_conversations(self):
        """The conversation list: days derived from the stored
        log (last 500 rows, grouped on the local calendar
        date), titled by each day's first user question."""
        try:
            rows = memory_store.get_recent_history(500)
        except Exception:  # noqa: BLE001
            return
        if not rows:
            self._add_system("No conversations yet.")
            return
        days = []
        by_day = {}
        for entry in rows:
            day = self._history_entry_date(entry)
            if day is None:
                continue
            by_day.setdefault(day, []).append(entry)
        for day in sorted(by_day, reverse=True):
            entries = by_day[day]
            title = ""
            for entry in entries:
                if str(entry.get("role") or "") == "user":
                    title = str(entry.get("content") or "").strip()
                    if title:
                        break
            if not title:
                first = str(entries[0].get("content") or "").strip()
                title = first.split("\n")[0] if first else ""
            if len(title) > 40:
                title = title[:37] + "…"
            today = datetime.date.today()
            if day == today:
                label = "Today"
            elif day == today - datetime.timedelta(days=1):
                label = "Yesterday"
            else:
                label = day.strftime("%a %d %b")
            days.append({
                "label": label,
                "title": title,
                "count": len(entries),
                "rows": entries,
            })
        if not days:
            self._add_system("No conversations yet.")
            return
        dlg = ConversationsDialog(
            self, days,
            on_open=lambda rows: self._chat_open_history_day(rows))
        try:
            dlg.exec()
        except Exception:  # noqa: BLE001
            pass

    def _chat_open_history_day(self, rows):
        """Open one conversation day as a history window."""
        self._chat_open_history_window(rows)

    # ------------------------------------------------------------ header
    def _refresh_chat_header(self):
        """The slim header row above the chat: the Pinned indicator
        (with a jump menu), and Back to latest (only while a
        history window is open). UI rethink Build 1: Conversations
        moved to the header overflow menu, so the row hides when
        neither button is visible — an empty header row is a void
        above the chat. The pinned count comes from the tree, so
        it is always the live count."""
        try:
            pinned = self._chat_thread().pinned_messages()
            pinned_visible = bool(pinned)
            btn = getattr(self, "_btn_pinned", None)
            if btn is not None:
                btn.setVisible(pinned_visible)
                btn.setText(f"Pinned ({len(pinned)})")
            back_visible = bool(
                getattr(self, "_chat_viewing_history", False))
            back = getattr(self, "_btn_back_latest", None)
            if back is not None:
                back.setVisible(back_visible)
            row = getattr(self, "_chat_header_row", None)
            if row is not None:
                # Never read isVisible() here: it is False while an
                # ancestor (this row, hidden at build) is hidden, so
                # the row could never become visible again. The
                # booleans above are the visibility.
                row.setVisible(pinned_visible or back_visible)
        except Exception:  # noqa: BLE001
            pass

    def _chat_show_pinned_menu(self):
        """The pinned jump menu: one row per pinned message,
        first line + time; tapping one jumps to it on its own
        branch."""
        try:
            from PySide6.QtWidgets import QMenu
            pinned = self._chat_thread().pinned_messages()
            if not pinned:
                return
            menu = QMenu(self)
            for node in pinned:
                first = (node.text or "").strip().split("\n")[0]
                if len(first) > 50:
                    first = first[:47] + "…"
                when = self._chat_node_time(node) or ""
                label = f"{first}  ·  {when}" if when else first
                menu.addAction(
                    label,
                    lambda _=False, _id=node.id: self._jump_to_message(_id))
            btn = getattr(self, "_btn_pinned", None)
            if btn is not None:
                menu.exec(btn.mapToGlobal(QPoint(0, btn.height())))
        except Exception:  # noqa: BLE001
            pass

    def _jump_to_message(self, msg_id: int):
        """Jump to one message on its own branch: steer the
        visible thread onto it, render, and glow it."""
        try:
            thread = self._chat_thread()
            if not thread.make_visible(msg_id):
                return
            self._render_visible_thread(scroll_to_msg_id=msg_id)
            bubble = self._chat_bubble_for(msg_id)
            if bubble is not None:
                self._glow_bubble(bubble)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ last seen
    def _chat_mark_last_seen(self):
        """Mark the newest message the user has read (the user
        is at the bottom and has read up to here). Two marks
        are written. ``chat/last_seen_id`` is the live tree's
        leaf node id — the position actually read, which covers
        voice turns that never reached the store (U4 #31).
        ``chat/last_seen_store_id`` is the newest stored row at
        or before that leaf — the durable key the restore-time
        hairline understands, since a tree id does not survive
        a restart. A leaf with its own store row marks that
        row; otherwise the newest stored row."""
        try:
            leaf = self._chat_thread().leaf()
            if leaf is not None and leaf.id:
                qsettings_set("chat/last_seen_id", int(leaf.id),
                              settings=self.settings)
            store_mark = None
            if leaf is not None \
                    and getattr(leaf, "store_id", None) is not None:
                store_mark = int(leaf.store_id)
            else:
                rows = memory_store.get_recent_history(1)
                if rows:
                    row_id = rows[0].get("id")
                    if row_id is not None:
                        store_mark = int(row_id)
            if store_mark is not None:
                qsettings_set("chat/last_seen_store_id", store_mark,
                              settings=self.settings)
        except Exception:  # noqa: BLE001
            pass

    def _chat_last_seen_id(self):
        try:
            value = qsettings_get("chat/last_seen_id",
                                  settings=self.settings)
            return int(value) if value is not None else None
        except Exception:  # noqa: BLE001
            return None
