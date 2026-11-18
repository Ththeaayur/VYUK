"""MainWindow chat mixin: the live reply (chat UI build, 2026-11-16).

Moves 1 and 2 (plus the voice-state line of move 13 and the
quiet rule of move 15): the reply streams word by word in time
with the voice, Stop replaces Send while it streams, a stopped
reply keeps the words already read with a Retry beside them, a
failed turn says so in plain words, and a regenerate keeps the
old version flippable instead of deleting it.

The streaming is real, never faked: chunks arrive from the
server through ``transcript_chunk_received`` (the live client's
``on_transcript_chunk``) and only whole words are ever revealed
— a trailing partial word is held until the next chunk completes
it or the turn finalises (``core.chat_thread.StreamingReveal``).
No timer, no dots that linger, no invented timing. The P2 speed
pass is untouched: the filler bridge, the latency recorder, and
the interrupt path all still live in the live client; Stop cuts
the audio through the same ``interrupt_playback`` the server
interruption path uses, and flags the session so no further text
for the turn lands.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton as _PB
from PySide6.QtWidgets import QWidget as _QW

from voice_assistant.core.gui_facade import audio_manager
from voice_assistant.core.gui_facade import chat_thread as _ct
from voice_assistant.core.gui_facade import live_session

from .style_tokens import ACCENT


class ChatStreamMixin:
    # ------------------------------------------------------------ model
    def _chat_thread(self):
        """The transcript tree (lazy — the window never touches
        it before the first chat action)."""
        thread = getattr(self, "_chat_thread_obj", None)
        if thread is None:
            thread = _ct.ChatThread()
            self._chat_thread_obj = thread
        return thread

    def _chat_version_note(self, msg_id: int, version: int) -> str:
        """The plain-words note for one version ("Edited — new
        answer", "Regenerated") — recorded by the window when
        the version was created, never invented here."""
        notes = getattr(self, "_chat_version_notes", None) or {}
        return notes.get((msg_id, version), "")

    def _note_chat_version(self, msg_id: int, version: int,
                           note: str):
        notes = getattr(self, "_chat_version_notes", None)
        if notes is None:
            notes = {}
            self._chat_version_notes = notes
        notes[(msg_id, version)] = note

    def _chat_note_send(self, text: str):
        """A send just left: tag it with the next turn
        generation, so a later Stop can name exactly which
        send it stopped (U4 #29) and a later failure can say
        which kind it was (U4 #30 — the kind rides the done
        callback, not this tag)."""
        try:
            gen = getattr(self, "_chat_turn_generation", 0) + 1
            self._chat_turn_generation = gen
            self._chat_send_generation = gen
            self._chat_send_text = text
        except Exception:  # noqa: BLE001
            pass

    def _chat_drop_stopped_arrival(self) -> bool:
        """True when an assistant arrival belongs to the send
        Stop just cancelled: the stopped generation is still
        the current one, so no newer send has superseded it
        and no new user turn has cleared it (see
        _on_transcript's user branch). A fresh send bumps the
        generation; a fresh spoken turn clears the marker."""
        try:
            stopped = getattr(self, "_chat_stopped_generation", None)
            if stopped is None:
                return False
            return stopped == getattr(self, "_chat_send_generation", 0)
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ live reply
    def _on_transcript_chunk(self, role: str, text: str):
        """One real chunk of the reply, as it arrives from the
        server. Only assistant chunks stream; planner progress
        lines never arrive here (they are local, through
        on_transcript) and are checked first there."""
        try:
            if role != "assistant" or not text:
                return
            live = getattr(self, "_live_reply", None)
            if (live is None or live.get("finalized")) \
                    and self._chat_drop_stopped_arrival():
                # Field-check fix #7 (U4 #29): a chunk of the
                # answer to the send Stop just cancelled — the
                # turn is over, so no bubble starts for it.
                return
            if live is None or live.get("finalized"):
                # First chunk of a reply: streaming starts BEFORE
                # the live bubble is created, so the chip row does
                # not rebuild mid-stream (move 15 — the quiet
                # rule: the first-message switch to follow-ups
                # waits for the turn) and the send button is Stop
                # from the first word. If no bubble can start,
                # streaming is undone again.
                self._chat_set_streaming(True)
                self._hide_thinking_bubble()
                bubble, msg_id = self._start_live_bubble()
                if bubble is None:
                    self._chat_set_streaming(False)
                    return
                live = {
                    "msg_id": msg_id,
                    "reveal": _ct.StreamingReveal(),
                    "bubble": bubble,
                    "stopped": False,
                    "finalized": False,
                }
                self._live_reply = live
                # The button derives its mode from the live reply,
                # which only exists now — re-sync so Stop shows
                # from the first word (the earlier sync, before
                # the bubble existed, still read Send).
                self._sync_send_button()
            if live.get("stopped"):
                return
            live["reveal"].feed(text)
            revealed = live["reveal"].revealed
            # The current word — the last whole word — is lit
            # with a soft accent; the rest stays plain. No
            # highlight wash, nothing pulses.
            html = _ct.lit_word_html(revealed, ACCENT)
            live["bubble"].update_stream(html, revealed)
            self._fit_chat_item(live["bubble"])
            self._scroll_chat_to_bottom()
        except Exception:  # noqa: BLE001 — a chunk never breaks chat
            pass

    def _start_live_bubble(self):
        """The bubble a live reply streams into. A regenerate /
        retry streams into the target bubble itself (the old
        version stays, flippable); a fresh reply gets a new
        bubble and a new streaming node in the tree."""
        try:
            target = getattr(self, "_pending_version_target", None)
            if target is not None:
                bubble = self._chat_bubble_for(target)
                if bubble is not None:
                    bubble.start_streaming()
                    self._chat_thread().set_status(
                        target, "streaming")
                    return bubble, target
                # The target bubble is gone (branch switched
                # under it): fall through to a fresh bubble.
                self._pending_version_target = None
            self._hide_thinking_bubble()
            bubble = self._add_live_bubble()
            if bubble is None:
                return None, None
            msg_id = getattr(bubble, "_chat_msg_id", None)
            if msg_id is not None:
                self._chat_thread().set_status(msg_id, "streaming")
            bubble.start_streaming()
            return bubble, msg_id
        except Exception:  # noqa: BLE001 — streaming never breaks chat
            return None, None

    def _add_live_bubble(self):
        """A fresh assistant bubble with no text yet, recorded
        in the tree as a streaming node. Returns the bubble (its
        ``_chat_msg_id`` is the node's id)."""
        try:
            before = len(self._chat_bubbles())
            self._add_bubble("VYUK", "", False, animate=True)
            bubbles = self._chat_bubbles()
            if len(bubbles) <= before:
                return None
            return bubbles[-1]
        except Exception:  # noqa: BLE001
            return None

    def _chat_bubble_for(self, msg_id: int):
        """The visible bubble rendering one tree node, or None
        when that node is not on the visible thread."""
        for bubble in self._chat_bubbles():
            if getattr(bubble, "_chat_msg_id", None) == msg_id:
                return bubble
        return None

    def _finalize_live_reply(self, final_text: str,
                             status: str = "done"):
        """The reply is over: the live bubble renders the full
        text through the normal pipeline (markdown, code
        blocks), the tree records it, and the button goes back
        to Send. A version target (regenerate / retry) takes
        the text as a new version of the same message."""
        try:
            # The reply is over — the turn is no longer in
            # flight, whatever path finalised it.
            self._chat_turn_in_flight = False
            live = getattr(self, "_live_reply", None)
            if live is None or live.get("finalized"):
                return
            live["finalized"] = True
            self._chat_set_streaming(False)
            thread = self._chat_thread()
            bubble = live.get("bubble")
            target = getattr(self, "_pending_version_target", None)
            if target is not None:
                self._pending_version_target = None
                thread.add_version(target, final_text)
                thread.set_status(target, status)
                node = thread.get(target)
                if bubble is not None:
                    bubble.finish_stream(final_text)
                    if node is not None and node.version_count > 1:
                        bubble.set_versions(
                            node.version_count, node.active,
                            self._chat_version_note(target,
                                                    node.active))
                    self._chat_apply_status_row(bubble, node)
                    self._chain_bubble_tab(bubble)
            else:
                msg_id = live.get("msg_id")
                if msg_id is not None:
                    thread.set_text(msg_id, final_text)
                    thread.set_status(msg_id, status)
                if bubble is not None:
                    bubble.finish_stream(final_text)
                    node = thread.get(msg_id) \
                        if msg_id is not None else None
                    self._chat_apply_status_row(bubble, node)
                    self._chain_bubble_tab(bubble)
            self._live_reply = None
            self._sync_send_button()
            self._scroll_chat_to_bottom()
        except Exception:  # noqa: BLE001 — finalising never breaks chat
            pass

    def _chat_apply_status_row(self, bubble, node):
        """The stopped / failed line under a finished reply, in
        plain words, with a Retry beside it. A done reply has
        no status row."""
        try:
            if bubble is None or node is None:
                return
            if node.status == "stopped":
                bubble.set_status(
                    "stopped", "Stopped — kept what was said",
                    show_retry=True)
            elif node.status == "failed":
                bubble.set_status(
                    "failed",
                    node.error or "That reply didn't arrive.",
                    show_retry=True)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ stop
    def _chat_send_mode(self) -> str:
        """What the send button does right now: "stop" while a
        turn is in flight — a reply streaming, a typed send
        still going, or the thinking bubble up — "send"
        otherwise. One button, one place, always visible. The
        mode is derived from live state on every sync, so a
        completed send or a landed reply flips it back without
        any thread hopping from the worker callback."""
        live = getattr(self, "_live_reply", None)
        if live is not None and not live.get("finalized"):
            return "stop"
        fut = getattr(self, "_typed_send_future", None)
        if fut is not None and not fut.done():
            return "stop"
        if getattr(self, "_thinking_row", None) is not None:
            return "stop"
        return "send"

    def _on_send_button(self):
        """The send button's one dispatch: Stop while a reply
        is streaming or a send is in flight, Send otherwise."""
        try:
            if self._chat_send_mode() == "stop":
                self._stop_live_reply()
            else:
                self._send_typed_message()
        except Exception:  # noqa: BLE001 — the button never breaks chat
            pass

    def _stop_live_reply(self) -> bool:
        """Stop the reply in flight. A streaming reply: cut the
        audio through the P2 hard cut, flag the session so no
        further text for the turn lands, and finalise the live
        bubble as stopped — the words already read stay, marked
        as stopped, with a Retry beside them. A typed send in
        flight: cancel the future (best effort — a send already
        running on the worker may still deliver, and a late
        answer is dropped by the stopped-generation marker,
        U4 #29, instead of landing as its own bubble) and drop
        the thinking bubble. Returns True when something was
        stopped."""
        try:
            # The turn ends here, from the user's side — the
            # in-flight flag clears whatever path follows.
            self._chat_turn_in_flight = False
            live = getattr(self, "_live_reply", None)
            if live is not None and not live.get("finalized"):
                try:
                    audio_manager.interrupt_playback()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    live_session.stop_reply()
                except Exception:  # noqa: BLE001
                    pass
                live["stopped"] = True
                self._finalize_live_reply(
                    live["reveal"].full, status="stopped")
                return True
            fut = getattr(self, "_typed_send_future", None)
            if fut is not None and not fut.done():
                try:
                    fut.cancel()
                except Exception:  # noqa: BLE001
                    pass
                self._typed_send_future = None
                # Field-check fix #7 (U4 #29): a send already
                # running on the worker may still deliver — its
                # late answer must not land as its own bubble.
                # The generation is recorded so the transcript
                # path can drop arrivals from this stopped send
                # (see _chat_drop_stopped_arrival); the session
                # is told too, so the client drops the rest of
                # the turn before it ever reaches the GUI.
                self._chat_stopped_generation = getattr(
                    self, "_chat_send_generation", 0)
                self._chat_stopped_text = getattr(
                    self, "_chat_send_text", None)
                try:
                    live_session.stop_reply()
                except Exception:  # noqa: BLE001
                    pass
                self._hide_thinking_bubble()
                # The turn may still complete server-side; a late
                # answer is dropped by the generation marker
                # above, not landed. One quiet word records that
                # the stop registered.
                self._add_system("Stopped.")
                self._sync_send_button()
                return True
            return False
        except Exception:  # noqa: BLE001 — stopping never breaks chat
            return False

    # ------------------------------------------------------------ retry / regenerate
    def _resend_parent_text(self, assistant_msg_id: int):
        """Resend the user text an assistant message answers,
        without adding a second user bubble: the echo is
        suppressed (see _on_transcript) and the reply is
        routed to the assistant message as a new version. The
        caller sets _pending_version_target first. Returns False
        when there is no parent user text to resend."""
        try:
            thread = self._chat_thread()
            node = thread.get(assistant_msg_id)
            if node is None:
                return False
            parent = thread.get(node.parent_id) \
                if node.parent_id is not None else None
            if parent is None or parent.role != "user":
                return False
            text = parent.text
            if not (text or "").strip():
                return False
            self._suppress_user_echo(text)
            self._note_self_sent(text)
            if self.worker is not None and self.worker.loop is not None \
                    and live_session.is_connected:
                _fut = live_session.send_text(text)
                import asyncio as _asyncio
                self._typed_send_future = \
                    _asyncio.run_coroutine_threadsafe(
                        _fut, self.worker.loop)
                self._chat_turn_in_flight = True
                self._chat_note_send(text)
                # The done callback marks the turn failed in the
                # transcript when the resend fails (see
                # _on_typed_send_done) — the pending version
                # target routes it to the right bubble, and the
                # kind it carries (U4 #30) says it was a resend.
                self._typed_send_future.add_done_callback(
                    lambda f: self._on_typed_send_done(f, kind="resend"))
                self._sync_send_button()
                return True
            return False
        except Exception:  # noqa: BLE001 — a failed resend is a failed turn
            return False

    def _regenerate_message(self, assistant_msg_id: int):
        """Regenerate: the same question asked again, the new
        answer a new version of the same message ("1 of 2").
        The old version stays, flippable."""
        try:
            thread = self._chat_thread()
            node = thread.get(assistant_msg_id)
            if node is None or node.role != "assistant":
                return
            if getattr(self, "_pending_version_target", None) \
                    is not None:
                return
            self._pending_version_target = assistant_msg_id
            if not self._resend_parent_text(assistant_msg_id):
                self._pending_version_target = None
                thread.set_status(
                    assistant_msg_id, "failed",
                    error="Couldn't ask again — not connected.")
                bubble = self._chat_bubble_for(assistant_msg_id)
                if bubble is not None:
                    self._chat_apply_status_row(
                        bubble, thread.get(assistant_msg_id))
            else:
                self._show_thinking_bubble()
        except Exception:  # noqa: BLE001 — regenerating never breaks chat
            pass

    def _retry_message(self, assistant_msg_id: int):
        """Retry: ask the same question again after a stopped
        or failed reply. The new answer is a new version of the
        same message — the stopped / failed attempt stays,
        flippable, never silently deleted."""
        try:
            thread = self._chat_thread()
            node = thread.get(assistant_msg_id)
            if node is None or node.role != "assistant":
                return
            if getattr(self, "_pending_version_target", None) \
                    is not None:
                return
            self._pending_version_target = assistant_msg_id
            if not self._resend_parent_text(assistant_msg_id):
                self._pending_version_target = None
                thread.set_status(
                    assistant_msg_id, "failed",
                    error="Couldn't retry — not connected.")
                bubble = self._chat_bubble_for(assistant_msg_id)
                if bubble is not None:
                    self._chat_apply_status_row(
                        bubble, thread.get(assistant_msg_id))
            else:
                self._show_thinking_bubble()
                bubble = self._chat_bubble_for(assistant_msg_id)
                if bubble is not None:
                    bubble.set_status("", "", show_retry=False)
        except Exception:  # noqa: BLE001 — retrying never breaks chat
            pass

    def _fail_pending_version_target(self, error: str):
        """A resend failed: the version target keeps its failed
        marker (with Retry) and the pending routing is cleared,
        so the next answer is never misrouted into it."""
        try:
            target = getattr(self, "_pending_version_target", None)
            if target is None:
                return
            self._pending_version_target = None
            thread = self._chat_thread()
            thread.set_status(target, "failed", error=error)
            bubble = self._chat_bubble_for(target)
            if bubble is not None:
                self._chat_apply_status_row(
                    bubble, thread.get(target))
        except Exception:  # noqa: BLE001
            pass

    def _on_typed_send_failed(self, text: str, kind: str = "typed"):
        """GUI-thread slot for a failed send. The kind rides the
        failure signal from the done callback (U4 #30): a
        resend failure belongs to the version target it was
        resending — it keeps its failed marker (with Retry)
        and no system line is posted for a send the user never
        typed. A typed failure is the user's own send failing:
        a system line in plain words, and the pending version
        target is left alone. An unknown kind is treated as
        typed: a failure is never attributed to a message it
        may not belong to. A resend whose target is already
        gone falls through to the system line too — a failure
        is never swallowed silently."""
        try:
            if kind == "resend" and getattr(
                    self, "_pending_version_target", None) is not None:
                self._fail_pending_version_target(text)
                return
            self._add_system(text)
        except Exception:  # noqa: BLE001
            pass

    def _chat_is_planner_line(self, text: str) -> bool:
        """True when an assistant line is planner progress, not
        the reply — the same formats _update_goal_pill folds
        (the stable emit contract pinned by test_v549_p29.py):
        "Running goal: … (budget: N step…)", "Goal step n: …
        [tool]", "Running plan <id>: … (N step(s)):", "Step
        n/N: …", and the terminal "Goal complete / stopped /
        halted", "Plan <id> finished:", "Plan stopped at step
        …" lines. A progress line never appends to a live
        bubble and never streams."""
        try:
            import re as _re
            line = str(text or "").strip()
            if not line:
                return False
            if _re.match(r"Running goal: .* \(budget: \d+ step", line):
                return True
            if _re.match(r"Running plan \S+: .* \(\d+ step", line):
                return True
            if _re.match(r"Goal step \d+: .*? \[", line):
                return True
            if _re.match(r"[✓✗–↻↪]?\s*Step \d+/\d+: ", line):
                return True
            if line.startswith(("Goal complete", "Goal stopped",
                                "Goal halted", "Goal finished",
                                "Plan stopped at step")):
                return True
            if _re.match(r"Plan \S+ finished:", line):
                return True
            return False
        except Exception:  # noqa: BLE001
            return False

    def _chat_turn_landed(self):
        """A reply landed: the turn is no longer in flight, and
        the send button re-derives (back to Send). A queued
        message sends now — the turn is over, and the queue
        flushes FIFO on turn end, not only when the voice status
        happens to change to LISTENING (a text turn may never
        move it)."""
        try:
            self._chat_turn_in_flight = False
            self._sync_send_button()
            self._chat_queue_flush()
        except Exception:  # noqa: BLE001
            pass

    def _chat_add_failed_reply(self, error: str):
        """A turn that died without a reply (the session dropped
        mid-turn): the failed attempt is kept as a message with
        a failed row and a Retry beside it — not erased, not a
        bare system line."""
        try:
            thread = self._chat_thread()
            node = thread.add_assistant(
                "", status="failed", error=error)
            self._hide_thinking_bubble()
            bubble = self._add_bubble(
                self._assistant_display_name(), "", is_user=False,
                animate=True, msg_id=node.id, record=False)
            if bubble is not None:
                # _add_bubble renders the failed row from the
                # node's status; only the tab chain is left.
                self._chain_bubble_tab(bubble)
            self._scroll_chat_to_bottom()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ quiet rule
    def _chat_set_streaming(self, streaming: bool):
        """While a reply streams, the transcript is the only
        thing that moves: the chip row is not rebuilt and the
        pill never pulses (move 15). The send button flips to
        Stop at the same moment. When streaming ends, the chip
        row rebuilds once — so the first reply still switches
        the starters to follow-ups, just after the reply
        instead of during it."""
        was = bool(getattr(self, "_chat_quiet_streaming", False))
        self._chat_quiet_streaming = bool(streaming)
        try:
            self._sync_send_button()
        except Exception:  # noqa: BLE001
            pass
        if was and not streaming:
            try:
                self._refresh_chip_row()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------ voice state line
    def _chat_voice_state_update(self, status_upper: str):
        """The quiet line above the composer (move 13): what
        the voice is doing right now — Listening, Thinking,
        Speaking, Dictating — in plain words, and nothing when
        it is doing nothing. The caption preview already shows
        the live draft of what was heard; this line is the
        state, not a second preview."""
        try:
            lbl = getattr(self, "_voice_state_lbl", None)
            if lbl is None:
                return
            if getattr(self, "_dictation_mode", False):
                text = "Dictating"
            elif "THINKING" in (status_upper or ""):
                text = "Thinking"
            elif "SPEAKING" in (status_upper or ""):
                text = "Speaking"
            elif "LISTENING" in (status_upper or ""):
                text = "Listening"
            else:
                text = ""
            if lbl.text() != text:
                lbl.setText(text)
            lbl.setVisible(bool(text))
        except Exception:  # noqa: BLE001 — the line never breaks chat
            pass

    # ------------------------------------------------------------ tab chain
    def _chain_bubble_tab(self, bubble):
        """Re-chain one bubble's Tab order: the bubble, its
        hover-row buttons in row order, its foot rows' buttons
        (status Retry, then the version flip), then the
        composer. Called at creation and whenever a foot row
        appears or disappears, so a keyboard user never skips
        a control that just showed up."""
        try:
            widgets = [bubble]
            widgets.extend(
                b for b in getattr(bubble, "_action_buttons", [])
                if b is not None and b.isVisible())
            _status_row = getattr(bubble, "_status_row", None)
            _retry = getattr(bubble, "_status_retry", None)
            if (_status_row is not None and _status_row.isVisible()
                    and _retry is not None and _retry.isVisible()):
                widgets.append(_retry)
            _version_row = getattr(bubble, "_version_row", None)
            if _version_row is not None and _version_row.isVisible():
                for w in _version_row.findChildren(_QW):
                    if isinstance(w, _PB) and w.isVisible():
                        widgets.append(w)
            _prev = None
            for w in widgets:
                if _prev is not None:
                    _QW.setTabOrder(_prev, w)
                _prev = w
            _cmd = getattr(self, "input_cmd", None)
            if _cmd is not None and _prev is not None:
                _QW.setTabOrder(_prev, _cmd)
        except Exception:  # noqa: BLE001 — tab order never breaks chat
            pass
