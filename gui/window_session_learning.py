"""MainWindow session mixin: learning, goal pill, acks, transcript.

Split from gui/window_session.py (GUI split #11 batch 1, move only).
"""

import re
import time

from voice_assistant.core.gui_facade import (
    failsafe,
)
from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from voice_assistant.core.gui_facade import config
from voice_assistant.core.gui_facade import qsettings_get, qsettings_set


class SessionLearningMixin:
    """Learning, goal pill, acks, transcript (GUI split #11 batch 1)."""

    # --------------------------------------- she learns you (v2.6)
    def _note_self_sent(self, text: str):
        """Remember that the window itself is about to inject `text`
        via send_text. Both session clients echo every send_text back
        through on_transcript as a "user" transcript — without this
        marker, VYUK would 'learn' from her own machinery (reminder
        triggers, watcher notices, briefing prompts). One marker is
        consumed by one echo; markers expire after 20 s."""
        try:
            pending = getattr(self, "_self_sent_texts", None)
            if pending is None:
                pending = self._self_sent_texts = []
            now = time.monotonic()
            pending[:] = [(t, exp) for (t, exp) in pending if exp > now]
            pending.append((text, now + 20.0))
        except Exception:  # noqa: BLE001
            pass

    def _consume_self_sent(self, text: str) -> bool:
        """True when `text` is the echo of a window-injected send."""
        try:
            pending = getattr(self, "_self_sent_texts", None)
            if not pending:
                return False
            now = time.monotonic()
            for i, (t, exp) in enumerate(pending):
                if exp > now and t == text:
                    del pending[i]
                    return True
            pending[:] = [(t, exp) for (t, exp) in pending if exp > now]
            return False
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _learning_module():
        """core.memory.learning, imported lazily and guarded (the pattern
        used for google_services / presence / watchers): a build
        without the module simply never learns — never a crash."""
        try:
            from voice_assistant.core.gui_facade import learning
            return learning
        except Exception:  # noqa: BLE001
            return None

    def _learn_from_utterance(self, text: str):
        """The learning choke point. Every genuine user utterance —
        spoken (session transcripts) or typed (send_text echoes back
        through _on_transcript in BOTH clients) — passes through
        _on_transcript exactly once, so this is the one place
        learning hooks in. Off means off: with the master toggle
        disabled the learner is never even called. Fully guarded —
        learning must never slow or break the chat path."""
        try:
            text = (text or "").strip()
            if not text:
                return
            if self._consume_self_sent(text):
                return  # our own injected send echoing back
            if not bool(qsettings_get("learning/enabled", settings=self.settings)):
                return
            module = self._learning_module()
            if module is None:
                return
            facts = module.learn_from_text(text) or []
            self._announce_learned(facts)
            self._announce_dropped_at_cap(module)
        except Exception:  # noqa: BLE001
            pass

    def _announce_learned(self, facts):
        """One quiet line per newly learned fact (max two per turn,
        then a pointer to Memory). The very first fact VYUK ever
        learns also says where to review or erase it."""
        displays = []
        for fact in facts or []:
            if isinstance(fact, dict):
                display = str(fact.get("display", "") or "").strip()
                if display:
                    displays.append(display)
        if not displays:
            return
        first_ever = not bool(
            qsettings_get("learning/announced_once", settings=self.settings))
        for i, display in enumerate(displays[:2]):
            line = f"Noted — I'll remember {display}."
            if first_ever and i == 0:
                line += " You can review or erase anything in Memory."
            self._add_system(line)
        extra = len(displays) - 2
        if extra > 0:
            self._add_system(
                f"…and {extra} more thing{'s' if extra != 1 else ''} — see Memory.")
        if first_ever:
            qsettings_set("learning/announced_once", True, settings=self.settings)

    def _announce_dropped_at_cap(self, module):
        """One line when a fact could not be kept: memory is full.
        Field-check fix #14 (T9): the 101st fact used to vanish
        silently — learn_from_text returns only what was stored.
        The dropped fact is named, so nothing the user taught
        disappears without a word, and the line points at Memory,
        where forgetting a fact makes room. Reads the drain
        defensively: a module without the signal simply has
        nothing to announce."""
        try:
            take = getattr(module, "take_dropped_at_cap", None)
            dropped = take() if callable(take) else []
        except Exception:  # noqa: BLE001
            dropped = []
        displays = []
        for fact in dropped or []:
            if isinstance(fact, dict):
                display = str(fact.get("display", "") or "").strip()
                if display:
                    displays.append(display)
        if not displays:
            return
        cap = getattr(module, "MAX_LEARNED", 100)
        line = (f"Memory is full ({cap} facts) — I couldn't keep "
                f"{displays[0]}. Forget something in Memory to make room.")
        extra = len(displays) - 1
        if extra > 0:
            line += f" ({extra} more couldn't be kept.)"
        self._add_system(line)

    # --------------------------------- goal pill (v5.49, item 29)
    def _set_goal_pill(self, text: str):
        pill = getattr(self, "goal_pill", None)
        label = getattr(self, "goal_pill_label", None)
        if pill is None or label is None:
            return
        label.setText(text)
        pill.setVisible(True)

    def _hide_goal_pill(self):
        pill = getattr(self, "goal_pill", None)
        if pill is not None:
            pill.setVisible(False)

    def _update_goal_pill(self, line: str):
        """v5.49 (programme item 29): fold ONE planner progress
        line into the goal pill. The line formats are the stable
        emit contract pinned by test_v549_p29.py (run_goal) and
        planner.run_plan: "Running goal: … (budget: N step(s)…)",
        "Goal step n: <words> [tool]", "Running plan <id>: …
        (N step(s)):", "Step n/N: <text>" (inside run_plan's
        ✓/✗/–/↻ step lines), and the terminal "Goal complete /
        stopped / halted", "Plan <id> finished:", "Plan stopped
        at step …" lines, which hide the pill. Anything else is
        not a progress line and leaves the pill alone."""
        text = str(line or "").strip()
        if not text or getattr(self, "goal_pill", None) is None:
            return
        m = re.match(r"Running goal: (.*) \(budget: (\d+) step", text)
        if m:
            self._goal_kind = "Goal"
            self._goal_total = int(m.group(2))
            self._goal_current = 0
            self._set_goal_pill(
                f"Goal — {m.group(1)} · step 0 of {self._goal_total}")
            return
        m = re.match(r"Running plan (\S+): (.*) \((\d+) step", text)
        if m:
            self._goal_kind = "Plan"
            self._goal_total = int(m.group(3))
            self._goal_current = 0
            self._set_goal_pill(
                f"Plan step 0 of {self._goal_total} — {m.group(2)}")
            return
        m = re.match(r"Goal step (\d+): (.*?) \[", text)
        if m:
            self._goal_current = int(m.group(1))
            total = self._goal_total or self._goal_current
            self._set_goal_pill(
                f"Goal step {self._goal_current} of {total} — "
                f"{m.group(2)}")
            return
        m = re.match(r"[✓✗–↻↪]?\s*Step (\d+)/(\d+): (.*)", text)
        if m:
            self._goal_kind = "Plan"
            self._goal_current = int(m.group(1))
            self._goal_total = int(m.group(2))
            action = m.group(3).split(" [")[0].rstrip()
            self._set_goal_pill(
                f"Plan step {self._goal_current} of "
                f"{self._goal_total} — {action}")
            return
        if text.startswith(("Goal complete", "Goal stopped",
                            "Goal halted", "Goal finished",
                            "Plan stopped at step")) or \
                re.match(r"Plan \S+ finished:", text):
            self._hide_goal_pill()

    def _on_goal_stop_clicked(self):
        """v5.49 (programme item 29): the pill's Stop — the hard
        stop, both halves the planner checks before every step:
        trip the failsafe (core/failsafe.trip_now) AND bump the
        session epoch (core/session_epoch.bump_epoch). Either
        one alone halts the run at its next step boundary; both
        together mean a run that somehow misses one signal still
        stops on the other. The pill hides at once — the run's
        own terminal line would hide it anyway."""
        try:
            failsafe.trip_now(
                "stopped from the goal pill (v5.49, programme item 29)")
        except Exception:  # noqa: BLE001 — the epoch bump still lands
            pass
        try:
            from voice_assistant.core.gui_facade import session_epoch

            session_epoch.bump_epoch(
                "goal pill stop (v5.49, programme item 29)")
        except Exception:  # noqa: BLE001
            pass
        self._hide_goal_pill()
        self._add_system("Stopped — the running goal or plan halts "
                         "at its next step.")

    # --------------------------------- v5.51 (E-1/X-3): instant ack
    def _ack_is_self_sent(self, text: str) -> bool:
        """Non-consuming peek at the self-sent markers (the same
        check _maybe_watch_command makes): True when `text` is the
        echo of a window-injected send_text. The consuming check
        stays _consume_self_sent's, down in _learn_from_utterance —
        this peek must never eat a marker."""
        try:
            pending = getattr(self, "_self_sent_texts", None) or []
            now = time.monotonic()
            return any(t == text and exp > now for (t, exp) in pending)
        except Exception:  # noqa: BLE001
            return False

    def _ack_receipt(self, text: str, typed_submit: bool = False):
        """The instant acknowledgement (v5.51, E-1/X-3): orb flare
        + the earcons' receipt blip, fired IMMEDIATELY and
        synchronously at FINAL command receipt — no brain
        dependency, no sleeps, no async hops; this runs inline at
        the receipt site and both effects are plain sync calls.

        Exactly two call sites, the two places a user turn commits:
          * _send_typed_message (typed_submit=True) — at submit,
            before the session send is even scheduled, so a typed
            command acks even with no session connected;
          * _on_transcript's role == "user" branch — where every
            client's FINAL voice transcript commits as a turn (and
            where a connected typed send's echo lands, deduped
            below). Wake is NOT a call site: _on_wake_word_activated
            keeps its own cue and moment, strictly upstream of any
            final transcript.

        Once per command: the dedupe token is (session epoch,
        typed-submit sequence, text). A typed submit bumps the
        sequence, so its later transcript echo carries the SAME
        token and is deduped, while a genuinely new submit of the
        same words gets a fresh token and acks again. A repeated
        delivery of the same voice transcript (same epoch, no new
        typed submit, same text) carries the same token too and
        does not double-ack. OFF (config.ack_enabled) means no
        flare, no sound, and the turn submits exactly as before.
        Fully guarded: an ack failure must never break receipt."""
        try:
            text = (text or "").strip()
            if not text:
                return
            if not bool(getattr(config, "ack_enabled", True)):
                return
            from voice_assistant.core.gui_facade import session_epoch

            if typed_submit:
                self._ack_typed_seq = \
                    getattr(self, "_ack_typed_seq", 0) + 1
            token = (session_epoch.current_epoch(),
                     getattr(self, "_ack_typed_seq", 0), text)
            if token == getattr(self, "_ack_last_token", None):
                return
            self._ack_last_token = token
            try:
                self.visualizer.ack_flare()
            except Exception:  # noqa: BLE001 — flare never breaks receipt
                pass
            try:
                self.earcons.ack()
            except Exception:  # noqa: BLE001 — sound never breaks receipt
                pass
        except Exception:  # noqa: BLE001
            pass

    def _on_transcript(self, role: str, text: str):
        # Chat UI (2026-11-16): a resend's echo (regenerate /
        # retry / edit) is a duplicate delivery — the turn is
        # already on screen — so it adds no second bubble, no
        # history entry, and no learning. The marker is consumed
        # by the first matching echo.
        if role == "user" and self._consume_suppressed_echo(text):
            return
        self._transcript_history.append({"role": role, "content": text})
        if role == "user":
            # Field-check fix #7 (U4 #29): a genuine new user
            # turn supersedes the send Stop just cancelled —
            # the stopped generation's marker clears, so the
            # reply to THIS turn is never dropped as a late
            # answer. The stopped send's own echo does not
            # clear it: it carries the stopped text. (A
            # suppressed resend echo never reaches here — it
            # returned above.)
            if (text or "") != getattr(
                    self, "_chat_stopped_text", None):
                self._chat_stopped_generation = None
            # Field-check fix #6 (U3 #15): command-shaped
            # utterances reach the confirm hook BEFORE dictation
            # staging can swallow them — a whole "stop dictating"
            # turns Dictation off, and a whole "yes"/"no" answers
            # a pending confirmation. Only while Dictation is on:
            # with it off the flow below is unchanged (staging
            # returns False, the confirm hook still runs after
            # the bubble, exactly as before).
            if getattr(self, "_dictation_mode", False):
                if self._chat_stop_dictation_command(text):
                    return
                if self._maybe_confirm_reply(text):
                    return
            # Chat UI (2026-11-16): dictation stages the words
            # in the composer as a draft for review — nothing is
            # ever sent unreviewed — and the transcript gets no
            # bubble for them.
            if self._chat_dictation_stage(text):
                return
            # v5.51 (E-1/X-3): a committed user turn acks HERE —
            # the voice path's receipt point (see _ack_receipt).
            # Bracketed lines and the window's own self-sent echoes
            # are VYUK's machinery, not user commands: no ack.
            if not (text or "").lstrip().startswith("[") \
                    and not self._ack_is_self_sent(text):
                self._ack_receipt(text)
            # Phase 2 (chatbox #17): the bubble below IS the record
            # of your words — the caption no longer duplicates them
            # under the orb. It only ever previews speech live,
            # before the final transcript lands as a bubble.
            # v5.17 (M18): genuine new words end the briefing pace.
            try:
                live_session.set_speech_speed_override(None)
            except Exception:  # noqa: BLE001
                pass
            # Chat UI (2026-11-16): spoken words land as text,
            # marked "spoken" — a small, quiet mark, not a badge.
            # Typed words carry no mark (their echo matches a
            # typed submit), and neither do the window's own
            # injected sends or bracketed machinery lines.
            _spoken = not (
                self._consume_typed_echo(text)
                or self._ack_is_self_sent(text)
                or (text or "").lstrip().startswith("["))
            # Chat UI (2026-11-16): a staged quote rides with
            # the message — the bubble shows the quote anchor
            # above its body.
            _quote_id = getattr(self, "_quoting_msg_id", None)
            _quote_text = None
            if _quote_id is not None:
                self._quoting_msg_id = None
                _node = self._chat_thread().get(_quote_id)
                if _node is not None:
                    _quote_text = _node.text
            self._add_bubble("You", text, is_user=True, spoken=_spoken,
                             quote_id=_quote_id, quote_text=_quote_text)
            # v5.48 (programme item 19): a whole-utterance yes/no
            # while a confirmation is pending answers THAT
            # confirmation and is consumed here — it is not fed
            # on as a brain command, not a watch phrase, and not
            # learned from. This branch is the hook's only entry:
            # role == "user" is the user's own transcript (see
            # _maybe_confirm_reply for why assistant/TTS text can
            # never reach it).
            if self._maybe_confirm_reply(text):
                return
            # EYES E6 (v5.45): watch-my-screen voice control —
            # an explicit watch phrase in the user's own words
            # flips the GUI-owned mode (see _maybe_watch_command
            # and core/desktop/screen_watch's header for why it rides
            # this surface: no registry tool exists for it).
            # v5.48 (programme item 21): an arm phrase while watch
            # is off is consumed too (the honest Settings reply) —
            # a consumed watch utterance is not learned from.
            if self._maybe_watch_command(text):
                return
            # She learns you (v2.6): spoken and typed utterances both
            # arrive here exactly once — see _learn_from_utterance.
            self._learn_from_utterance(text)
        else:
            # v5.49 (programme item 29): planner progress reaches
            # the GUI on THIS channel — every client's run_plan /
            # run_goal emit callback is its on_transcript — so the
            # goal pill folds each assistant line in here, on the
            # same path, before the line lands as a bubble as usual.
            self._update_goal_pill(text)
            # Chat UI (2026-11-16): planner progress lines are
            # checked FIRST and never append to a live bubble —
            # they are not the reply.
            if self._chat_is_planner_line(text):
                self._hide_thinking_bubble()
                self._add_bubble(config.assistant_name, text,
                                 is_user=False)
                self._sync_send_button()
                return
            # Chat UI (2026-11-16): the final text reconciles
            # with the live reply. When it matches what streamed
            # (the usual case — the same buffer), the live bubble
            # finalises with it and no second bubble appears.
            # When it does not match (a text turn with no chunks,
            # or a part.text arrival), the live bubble finalises
            # with what it showed and this text lands as its own
            # bubble below it.
            _live = getattr(self, "_live_reply", None)
            _had_live = _live is not None
            if _live is not None and not _live.get("finalized"):
                _streamed = _live["reveal"].full
                # The streamed buffer and the final transcript are
                # the same reply when they match apart from
                # trailing whitespace: a chunk boundary leaves a
                # trailing space in the buffer ("hello ") that the
                # flushed transcript does not carry ("hello").
                # Comparing raw would fork one reply into two
                # bubbles — the live one finalised with the raw
                # buffer, trailing space and all — and leave the
                # final text landing as a duplicate below it.
                if _streamed and _streamed.rstrip() == text.rstrip():
                    self._finalize_live_reply(text, status="done")
                    self._chat_turn_landed()
                    return
                if _streamed:
                    self._finalize_live_reply(_streamed,
                                              status="done")
                else:
                    # A live bubble with nothing yet said: drop
                    # it (the thinking bubble already went) and
                    # let this text land as the reply. The row
                    # goes with the bubble — a bare layout would
                    # leave a gap in the transcript.
                    self._live_reply = None
                    self._chat_set_streaming(False)
                    _bubble = _live.get("bubble")
                    if _bubble is not None:
                        try:
                            _idx = self._chat_row_index_for(_bubble)
                            if _idx is not None:
                                _item = self.chat_layout.takeAt(_idx)
                                if _item is not None \
                                        and _item.layout() is not None:
                                    self._delete_layout_items(
                                        _item.layout())
                            else:
                                _bubble.deleteLater()
                        except Exception:  # noqa: BLE001
                            pass
                        _msg_id = _live.get("msg_id")
                        if _msg_id is not None:
                            _thread = self._chat_thread()
                            _node = _thread.get(_msg_id)
                            if _node is not None and not _node.text.strip():
                                # The empty streaming node leaves
                                # the tree; the reply below takes
                                # its place.
                                _thread.delete_message(_msg_id)
            # Field-check fix #7 (U4 #29): a final transcript
            # with no live reply in flight is the late answer
            # to the send Stop just cancelled — drop it instead
            # of landing it as its own bubble after "Stopped.".
            # A final that reconciles a live reply in flight
            # (_had_live) is this turn's own reply, never
            # dropped. The turn already ended at the stop, so
            # the landing bookkeeping still runs (a queued
            # message sends now).
            if not _had_live and self._chat_drop_stopped_arrival():
                self._chat_turn_landed()
                return
            # A real VYUK reply replaces the thinking-dots bubble.
            self._hide_thinking_bubble()
            self._add_bubble(config.assistant_name, text, is_user=False)
            self._chat_turn_landed()
