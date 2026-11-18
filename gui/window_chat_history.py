"""MainWindow chat mixin: history (GUI split #9, move only).

History restore/clear, entry dates, clear-history. Bodies moved
verbatim from gui/window_chat.py; behaviour is unchanged.
"""

import datetime

from PySide6.QtCore import QTimer

from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import memory_store
from voice_assistant.core.gui_facade import qsettings_get


class ChatHistoryMixin:
    # --------------------------------------- chat history (v2.5)
    def _restore_chat_history(self):
        """Put the most recent conversation back on screen at launch.

        The sessions themselves already write every transcript to
        the store's conversation_logs (both clients pair each
        on_transcript with a log_message), so this only READS: the
        last 30 entries, under one quiet date divider. Any failure
        here must never block startup — worst case, a fresh chat."""
        try:
            entries = memory_store.get_recent_history(30)
        except Exception:  # noqa: BLE001
            return
        if not entries:
            return
        try:
            # Phase 2 (chatbox #8/#9): restored bubbles carry their
            # real stored times, and a divider is inserted whenever
            # the calendar day changes inside the restored block —
            # not one label for the whole block. Bulk load: bubbles
            # land instantly, never animated.
            self._chat_bulk_loading = True
            self._chat_new_since_lbl = None
            try:
                last_day = None
                today = datetime.date.today()
                for entry in entries:
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
                        self._add_system(label)
                    when = self._history_entry_time(entry)
                    _dimmed = day is not None and day != today
                    _store_id = entry.get("id")
                    if role == "user":
                        self._add_bubble("You", content, is_user=True,
                                         animate=False, time_str=when,
                                         dimmed=_dimmed,
                                         store_id=_store_id)
                    elif role == "assistant":
                        self._add_bubble(config.assistant_name, content,
                                         is_user=False, animate=False,
                                         time_str=when,
                                         dimmed=_dimmed,
                                         store_id=_store_id)
                if last_day is not None:
                    # The live divider must not repeat the newest
                    # restored day.
                    self._last_chat_day = last_day
                # Chat UI (2026-11-16): the "New since" hairline —
                # everything after the last message the user had
                # read when the app last closed. The hairline sits
                # after that message; the view settles on it
                # instead of the bottom, so the user lands where
                # they left off.
                self._chat_new_since_id = self._chat_insert_new_since(
                    entries)
            finally:
                self._chat_bulk_loading = False
        except Exception:  # noqa: BLE001
            self._chat_bulk_loading = False
        # UI-feel pass: the per-bubble scrolls fire before the
        # final layout settles, which could leave the restored
        # view parked mid-history with a bubble sliced at the
        # top edge. One unconditional settle, after layout — on
        # the "New since" hairline when one was placed, else at
        # the bottom.
        def _settle() -> None:
            try:
                bar = self.chat_scroll.verticalScrollBar()
                _marker = getattr(self, "_chat_new_since_lbl", None)
                if _marker is not None:
                    self.chat_scroll.ensureWidgetVisible(
                        _marker, 0, 40)
                else:
                    bar.setValue(bar.maximum())
                    # Nothing new to read — the newest message
                    # is seen, so the next launch has no hairline.
                    self._chat_mark_last_seen()
            except Exception:  # noqa: BLE001
                pass

        QTimer.singleShot(160, _settle)
    def _chat_insert_new_since(self, entries):
        """The "New since <date>" hairline (chat UI, 2026-11-16):
        after the last message the user had read when the app
        last closed, so the restore lands where they left off.
        Returns the last-seen id when a hairline was placed,
        else None. Nothing is placed when there is no last-seen
        id, when it is not among the restored entries, or when
        it already is the newest entry."""
        try:
            from PySide6.QtWidgets import QLabel as _QLabel
            from PySide6.QtCore import Qt as _Qt
            # Field-check fix #7 (U4 #31): the restore-time
            # hairline only understands store ids — read the
            # durable store mark first; the live-tree mark in
            # chat/last_seen_id does not survive a restart.
            # Marks written before the store key existed (and
            # marks set directly, as the hairline test does)
            # fall back to the single key.
            last_seen = None
            try:
                last_seen = qsettings_get(
                    "chat/last_seen_store_id", settings=self.settings)
            except Exception:  # noqa: BLE001
                last_seen = None
            if last_seen is None:
                last_seen = self._chat_last_seen_id()
            if last_seen is None or not entries:
                return None
            _ids = [e.get("id") for e in entries]
            if last_seen not in _ids or last_seen == _ids[-1]:
                return None
            _seen_entry = next(
                e for e in entries if e.get("id") == last_seen)
            _day = self._history_entry_date(_seen_entry)
            _label = ("New since " + _day.strftime("%a %d %b")
                      if _day is not None else "New since last visit")
            # The bubble rendering the last-seen entry, by its
            # stored row id.
            _bubble = None
            for _b in self._chat_bubbles():
                _node = self._chat_thread().get(
                    getattr(_b, "_chat_msg_id", None) or -1)
                if _node is not None and _node.store_id == last_seen:
                    _bubble = _b
                    break
            if _bubble is None:
                return None
            _idx = self._chat_row_index_for(_bubble)
            if _idx is None:
                return None
            lbl = _QLabel(_label)
            lbl.setObjectName("chatDivider")
            lbl.setAlignment(_Qt.AlignmentFlag.AlignCenter)
            self.chat_layout.insertWidget(_idx + 1, lbl)
            self._chat_new_since_lbl = lbl
            return last_seen
        except Exception:  # noqa: BLE001 — the hairline never breaks restore
            return None

    @staticmethod
    def _history_entry_date(entry) -> datetime.date | None:
        """The local calendar date of a conversation_logs entry. The
        table's timestamp is SQLite CURRENT_TIMESTAMP (UTC), so it is
        converted to local time before the date is taken."""
        raw = str((entry or {}).get("timestamp") or "").strip()
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
    def _history_entry_time(entry) -> str | None:
        """The local HH:MM of a conversation_logs entry (its
        stored timestamp is UTC), or None when unparseable —
        restored bubbles show their real time (chatbox #8)."""
        raw = str((entry or {}).get("timestamp") or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone().strftime("%H:%M")
    def _clear_chat_view(self):
        """Empty the on-screen chat (bubbles, cards, system lines) —
        the layout's leading stretch (index 0) stays."""
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                self._delete_layout_items(item.layout())
        self._chat_has_messages = False
        self._last_nova_bubble = None
        # Phase 2 (chatbox #7): grouping state must not point at
        # bubbles that no longer exist — a stale _last_bubble
        # would be called on a deleted widget by the next add.
        self._last_bubble = None
        self._last_bubble_user = None
        self._group_break = True
        # A cleared chat is a fresh conversation: the next bubble
        # gets its day divider again, even on the same day the
        # restored history ended on.
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
        # Chat UI (2026-11-16): a cleared chat is a fresh
        # conversation in the tree too — a stale tree would
        # re-render the cleared messages on the next branch
        # switch. The live reply, the pending version target,
        # and the queue die with it; the undo row and the
        # new-since marker go as well.
        try:
            self._chat_thread().clear()
        except Exception:  # noqa: BLE001
            pass
        self._live_reply = None
        self._pending_version_target = None
        self._chat_queue = []
        self._chat_new_since_lbl = None
        try:
            self._clear_deleted_undo()
        except Exception:  # noqa: BLE001
            pass
    @staticmethod
    def _delete_layout_items(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                ChatHistoryMixin._delete_layout_items(item.layout())  # GUI split #9 INTENTIONAL: recursive call repointed from ChatMixin (same staticmethod object via the facade composition; the old class name does not exist in this module and importing the facade back would cycle)  # v5.50 item 43: was MainWindow._delete_layout_items (same staticmethod, now on this mixin)
    def _clear_chat_history(self):
        """Two-tap clear (the offline-pack pattern): the first tap
        arms, a second within ~4 s wipes the stored conversation
        and the on-screen chat with it. Build 6: the button left
        the Memory page for the "···" overflow, so the arming is
        reported on the page's status line instead of a button."""
        if not getattr(self, "_history_clear_armed", False):
            self._history_clear_armed = True
            if hasattr(self, "btn_clear_history"):
                self.btn_clear_history.setText("Tap again to clear")
            label = getattr(self, "mem_export_label", None)
            if label is not None:
                label.setText(
                    "Tap again to clear the conversation history.")
                label.setVisible(True)
            QTimer.singleShot(4000, self._disarm_clear_history)
            return
        self._history_clear_armed = False
        if hasattr(self, "btn_clear_history"):
            self.btn_clear_history.setText("Clear conversation history")
        self._hide_clear_history_note()
        try:
            # core.memory.memory has no dedicated clear for conversation_logs;
            # the deletion goes through the store's own connection
            # helper — the same one every store write uses — rather
            # than a second sqlite connection from the GUI.
            memory_store._execute("DELETE FROM conversation_logs")
        except Exception as exc:  # noqa: BLE001
            print(f"[History] clear failed: {exc}")
        self._transcript_history.clear()
        self._clear_chat_view()
        # An emptied chat is the empty state again: full hero,
        # starter chips (Phase 2, chatbox #1/#18).
        self._chat_has_messages = False
        self._refresh_chip_row()
        self._set_hero_collapsed(False)
        self._add_system("Conversation history cleared.")
    def _disarm_clear_history(self):
        if not getattr(self, "_history_clear_armed", False):
            return
        self._history_clear_armed = False
        if hasattr(self, "btn_clear_history"):
            try:
                self.btn_clear_history.setText("Clear conversation history")
            except RuntimeError:
                pass
        self._hide_clear_history_note()

    def _hide_clear_history_note(self):
        """Hide the arming note — only when it still shows the
        arming text, so an export/import note is never clobbered."""
        label = getattr(self, "mem_export_label", None)
        if label is not None and label.text() == (
                "Tap again to clear the conversation history."):
            label.setVisible(False)
