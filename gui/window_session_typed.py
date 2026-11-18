"""MainWindow session mixin: typed send + tool frames.

Split from gui/window_session.py (GUI split #11 batch 2, move only).
"""

import asyncio
import logging

from voice_assistant.core.gui_facade import live_session  # Phase 3 B2h: brain hub moved to core/brain/brain.py
from voice_assistant.core.gui_facade import vision_manager


# Chatfix H1: logger for the typed-send done callback. The w9
# error ladder's own log line does not carry the exception text,
# so the callback logs the failure itself, with traceback — that
# line is what the H1 diagnosis needs to find what threw.
_log = logging.getLogger("nova.gui.window_session")


# v5.53 (F8): how long the tool frame sink waits to learn
# whether a scheduled frame delivery actually landed. The
# send is a local session write — sub-second in honest
# operation — and a screenshot must not stall a turn, so the
# wait is short; an unconfirmed delivery is NOT vision (the
# honest text path takes the result instead).
_FRAME_DELIVERY_WAIT_S = 1.5


def _await_frame_delivery(future, loop,
                          timeout: float = _FRAME_DELIVERY_WAIT_S) -> str:
    """Observe a scheduled send_image_frame future and return
    its delivery status (v5.53, F8): the session's own status
    string ("delivered" / "degraded" / "no-session" /
    "error"), "timeout" when the wait expired, or
    "unconfirmed" when the outcome could not be observed at
    all. Never raises.

    Deadlock guard: when the CALLING thread is running the
    very loop the frame was scheduled on, blocking here would
    freeze that loop (the delivery could never run), so the
    outcome is only logged when it lands and the delivery
    reads "unconfirmed" — which the sink maps to the honest
    non-vision path, never to a vision claim."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running is loop:
        def _note_late(fut):
            try:
                status = fut.result()
            except Exception as e:  # noqa: BLE001
                status = f"error ({e})"
            print(f"[Vision] frame delivery finished after "
                  f"the tool result was settled: {status}")

        try:
            future.add_done_callback(_note_late)
        except Exception:  # noqa: BLE001
            pass
        return "unconfirmed"
    try:
        status = future.result(timeout=timeout)
    except TimeoutError:
        print(f"[Vision] frame delivery unconfirmed after "
              f"{timeout:.1f}s (the send may still land; the "
              "tool result does not claim vision)")
        return "timeout"
    except Exception as e:  # noqa: BLE001 — a raised send is a
        return f"error ({e})"  # status, not a crash
    return status if isinstance(status, str) and status else "error"


class SessionTypedMixin:
    """Typed send + tool frames (GUI split #11 batch 2)."""
    def _plan_approval_hook(self, task, preview):
        """Fix 8 (plan approval): the coordinator's plan hook
        for typed turns — the human sees the plan once, on
        the GUI thread, before the task runs. Returns
        {"approved": [...], "outcome": ...} (the dict form
        the coordinator reads — "timeout" when the dialog's
        own silence timer fired, "ask_each_time" for an
        explicit refusal or close, "approved" when steps
        were approved). Any failure reads as [] — "ask each
        time": the hook never approves by itself, and
        core/plan_approval.approve_plan re-validates the
        returned indices before recording anything. A spoken
        "yes" never reaches this method."""
        try:
            from voice_assistant.gui.plan_approval_dialog import (
                present_plan_approval_dialog_ex)
        except Exception:  # noqa: BLE001 — no dialog: ask per step
            return []
        try:
            indices, outcome = present_plan_approval_dialog_ex(
                self, getattr(task, "title", "") or "task", preview)
        except Exception:  # noqa: BLE001 — a broken dialog asks per step
            return []
        return {"approved": list(indices or []),
                "outcome": outcome or "ask_each_time"}

    def _send_typed_message(self):
        text = self.input_cmd.text().strip()
        if not text:
            return
        # Chat UI (2026-11-16): an edit in progress commits
        # here — Enter saves the edit instead of sending a new
        # message. The commit owns the composer from here on.
        if getattr(self, "_editing_msg_id", None) is not None:
            self._commit_edit()
            return
        # S12 (C2): remember what was sent so Up can recall it.
        hist = getattr(self, "_sent_history", None)
        if hist is None:
            hist = self._sent_history = []
        hist.append(text)
        del hist[:-50]
        # Conversion Wave 9: conversation check as DIAGNOSTICS ONLY
        # on the live typed-turn path — never a turn blocker.
        try:
            from voice_assistant.core.gui_facade import w9_wiring as _w9
            _w9.diagnose_turn(self, text)
        except Exception:  # noqa: BLE001 — diagnostics never break send
            pass
        self._recall_idx = None
        self.input_cmd.clear()
        # v5.51 (E-1/X-3): the typed path acks at SUBMIT — inline,
        # before the send is scheduled and whether or not a session
        # is connected (see _ack_receipt). The connected send's
        # transcript echo is deduped against this same token.
        self._ack_receipt(text, typed_submit=True)
        # Chat UI (2026-11-16): the echo of this submit is typed,
        # not spoken — the bubble it lands as carries no "spoken"
        # mark (see _on_transcript). Guarded: the session mixin
        # also stands alone in the chatfix harness, where the
        # chat mixins are absent and there is no bubble to mark.
        _note_echo = getattr(self, "_note_typed_echo", None)
        if _note_echo is not None:
            _note_echo(text)
        # Task/Turn Coordinator (identity-gap P0): explicit
        # saved-plan / routine invocations execute locally with a
        # task id + receipt, and the turn must NOT also go live.
        # Every other turn falls through unchanged, and ANY hook
        # failure falls through too — a coordinator failure must
        # never break sending (same principle as the w9
        # diagnostics hook above).
        try:
            from voice_assistant.core.gui_facade import task_coordinator as _tc  # Phase 3 B1a-2: moved to core/brain/
            if _tc.handle_typed_turn(
                    self, text, plan_hook=self._plan_approval_hook):
                return
        except Exception:  # noqa: BLE001 — coordinator never breaks send
            pass
        # Chat UI (2026-11-16): type while she's speaking and
        # the words wait their turn — the message queues (its
        # bubble shows at once, marked Queued) instead of
        # cutting across her reply. A coordinator-handled turn
        # returns above, so a local execution never queues.
        # Guarded like the echo note above: the bare session
        # mixin has no queue, so it sends at once.
        _chat_mid = getattr(self, "_chat_is_mid_reply", None)
        if _chat_mid is not None and _chat_mid():
            _queue = getattr(self, "_chat_queue_typed", None)
            if _queue is not None:
                _queue(text)
                return
        if self.worker and self.worker.loop and live_session.is_connected:
            # Chatfix H1 (v5.93 typed-chat silence): the pre-fix
            # code dropped the future this call returns, so any
            # exception inside send_text vanished with zero UI
            # trace — the typed message produced pure silence.
            # Capture it and observe it: the callback below
            # surfaces a failure as a log line + a chat system
            # message, and does nothing on success, so the
            # success path is unchanged.
            # Chatfix2 diagnostic: snapshot the send-time state
            # that settles M1 vs M2 (session dropped before
            # execution vs inner callback None at echo time).
            # The echo flag is reset here so a proxy-level no-op
            # (dropped session, inner.send_text never runs)
            # cannot inherit a stale True from an earlier send;
            # inner.send_text sets it True only when the echo
            # call actually executes. The snapshot (not a live
            # re-read) is passed to the done callback so the line
            # describes this send even if the inner is swapped
            # before the callback runs. Diagnostic only.
            _diag_inner = getattr(live_session, "_inner", None)
            _diag_cb = (getattr(_diag_inner, "on_transcript", None)
                        if _diag_inner is not None else None)
            _diag = (bool(live_session.is_connected),
                     _diag_cb is None, _diag_inner)
            try:
                if _diag_inner is not None:
                    _diag_inner._diag_typed_echo_ran = False
            except Exception:  # noqa: BLE001 — diagnostics never break send
                pass
            _send_future = asyncio.run_coroutine_threadsafe(
                live_session.send_text(text), self.worker.loop)
            # Chat UI (2026-11-16): the future is kept, not
            # dropped — Stop cancels it (best effort), and the
            # send button's mode reads it. Cleared by the done
            # callback below.
            self._typed_send_future = _send_future
            self._chat_turn_in_flight = True
            # Field-check fix #7 (U4 #29/#30): tag the send
            # with its turn generation, and tell the done
            # callback it was a typed send (a resend carries
            # "resend" instead) so a failure is never routed
            # to the wrong message. Guarded like the echo note
            # above: the session mixin also stands alone in
            # the chatfix harness, where the chat mixins are
            # absent and there is no generation to tag.
            _note_send = getattr(self, "_chat_note_send", None)
            if _note_send is not None:
                _note_send(text)
            _send_future.add_done_callback(
                lambda fut, diag=_diag: self._on_typed_send_done(
                    fut, diag, kind="typed"))
            self.earcons.sent()
        else:
            # Conversion Wave 9: the error ladder advances on this
            # send failure (diagnostic record + log sink only).
            try:
                from voice_assistant.core.gui_facade import w9_wiring as _w9e
                _w9e.note_turn_error(self, "not connected yet")
            except Exception:  # noqa: BLE001
                pass
            self._add_system("You're not connected yet — press Connect first.")
            # S6 (behavioral: offline draft): the composer was cleared
            # at submit, before the connection check (UI-05) — so an
            # offline send used to lose the draft from the composer
            # (it survived only in Up-arrow history). Restore it here
            # so the draft stays editable in place and the user can
            # press Connect and send without retyping. The history
            # entry and the submit ack above are unchanged, and a
            # coordinator-handled turn returns before this branch,
            # so an executed turn never leaves its text behind.
            self.input_cmd.setText(text)

    def _on_typed_send_done(self, future, diag=None, kind="typed"):
        """Done callback for the typed send (chatfix H1 diagnostic).

        Runs on the worker loop thread, so it never touches widgets:
        the failure is recorded on the existing w9 error ladder,
        logged with traceback, and emitted through the bridge signal
        whose GUI-thread slot posts the system message. A succeeding
        send returns here and does nothing except the chatfix2 diag
        line below — the success path (ack at submit, sent sound,
        transcript echo) is otherwise byte-identical to before. A
        cancelled send is also silent by design: session teardown
        cancels pending work, and that is not a failure to report.

        Field-check fix #7 (U4 #30): ``kind`` says which send
        this was — "typed" for a user's own send, "resend" for
        a regenerate / retry / edit resend — and it rides the
        failure signal with the text, so the GUI slot routes
        the failure to the send that actually failed instead
        of inferring it from the pending version target.

        Chatfix2: when diag is given (a snapshot tuple from
        _send_typed_message: connected, inner-callback-None,
        inner object), one diag line is logged for every completed
        send — success as well as failure — recording whether the
        echo call ran (read from the inner's _diag_typed_echo_ran
        flag). That line settles M1 (session dropped: connected
        True, callback present, echo_ran False, no exception) vs
        M2 (callback None: echo_ran False with
        inner_on_transcript_none True). Level is warning, not
        info, deliberately: nothing in the tree configures logging,
        so the default WARNING level governs, and an info line
        would be invisible in the app log. The line is not an
        error; it is the one line the field diagnosis needs.
        """
        # Chat UI (2026-11-16): the send is no longer in
        # flight — clear the stored future (only when it is
        # this one; a newer send may already have stored its
        # own). The button mode re-derives from live state on
        # the next GUI-thread sync (_on_transcript / _on_status
        # / composer typing), so no thread hopping here. A
        # cancelled future clears too — done() is True for it,
        # so the mode is already right, but the reference
        # must not linger for the next Stop to read.
        try:
            if getattr(self, "_typed_send_future", None) is future:
                self._typed_send_future = None
        except Exception:  # noqa: BLE001
            pass
        try:
            exc = future.exception()
        except Exception:  # noqa: BLE001 — cancelled future
            return
        if diag is not None:
            _connected, _cb_none, _inner = diag
            _echo = (getattr(_inner, "_diag_typed_echo_ran", False)
                     if _inner is not None else False)
            _log.warning("typed send diag: connected=%s "
                         "inner_on_transcript_none=%s echo_ran=%s",
                         _connected, _cb_none, bool(_echo))
        if exc is None:
            # Phase 1 (F04): a completed send can still have been
            # DROPPED — the brain layer's delivery result, not an
            # exception. This is the gap the submit-time
            # not-connected branch cannot cover: it already
            # renders its own message (and restores the draft)
            # when nothing is live at submit, but a session that
            # dies between submit and execution used to complete
            # "successfully" with no echo and no answer. A dropped
            # result now gets the same visible treatment as a
            # failed send, with retry guidance.
            try:
                result = future.result()
            except Exception:  # noqa: BLE001 — cancelled future
                return
            if isinstance(result, dict) and result.get("status") == "dropped":
                try:
                    from voice_assistant.core.gui_facade import w9_wiring as _w9e
                    _w9e.note_turn_error(
                        self, "typed send dropped: not connected")
                except Exception:  # noqa: BLE001
                    pass
                text = ("Your message didn't go through — you're "
                        "no longer connected. Press Connect, then "
                        "send again.")
                try:
                    signals = getattr(self, "signals", None)
                    if signals is not None and hasattr(
                            signals, "typed_send_failed"):
                        signals.typed_send_failed.emit(text, kind)
                    else:
                        self._add_system(text)
                except Exception:  # noqa: BLE001
                    pass
            return
        detail = "%s: %s" % (type(exc).__name__, exc)
        # Existing diagnostic record for send failures: the same
        # w9 error-ladder sink the not-connected branch uses.
        try:
            from voice_assistant.core.gui_facade import w9_wiring as _w9e
            _w9e.note_turn_error(self, "typed send failed: " + detail)
        except Exception:  # noqa: BLE001 — diagnostics never break delivery
            pass
        # The ladder's own log line does not carry the exception
        # text, so log it here with traceback: this line is the
        # trigger the H1 diagnosis needs to find what threw.
        _log.error("typed send failed: %s", detail,
                   exc_info=(type(exc), exc, exc.__traceback__))
        text = ("Your message didn't go through — the typed send "
                "failed (%s). Please try again." % detail[:200])
        try:
            signals = getattr(self, "signals", None)
            if signals is not None and hasattr(signals, "typed_send_failed"):
                signals.typed_send_failed.emit(text, kind)
            else:
                # No bridge (should not happen once the window is
                # built): post directly rather than stay silent.
                self._add_system(text)
        except Exception:  # noqa: BLE001 — failure path must never raise
            pass

    def _send_screen_capture(self):
        if not (self.worker and self.worker.loop and live_session.is_connected):
            self._notify("Connect first, then I can look at your screen.")
            return
        img_bytes, mime = vision_manager.capture_screen()
        self.earcons.capture()
        # v5.45 (H12): a frame handed to the model is untrusted
        # content — note it for the injection taint, as the tools_handlers.py
        # frame sink already does for tool-path frames.
        try:
            from voice_assistant.core.gui_facade import note_image_frame
            note_image_frame()
        except Exception:  # noqa: BLE001 — bookkeeping never breaks delivery
            pass
        asyncio.run_coroutine_threadsafe(live_session.send_image_frame(img_bytes, mime), self.worker.loop)
        self._add_system("Screen shared with VYUK")

    def _deliver_tool_frame(self, image_bytes: bytes, mime_type: str = "image/jpeg"):
        """Frame sink for the funnel registry (v5.42 "Truth", E1; set_frame_sink in core/funnel/tools_handlers.py), registered
        in __init__ via tools.set_frame_sink — this is how a
        take_screenshot / screen_look frame reaches the model.

        Called SYNCHRONOUSLY from tool code. For a
        native-vision session the frame goes through the
        session's send_image_frame, scheduled on the worker
        loop with run_coroutine_threadsafe — and, since v5.53
        (F8), OBSERVED: tool calls execute off the receive
        loop (v5.44, H2), so the sink waits a short bounded
        time (_await_frame_delivery) for the send's own
        delivery status, and returns None ONLY when the
        status is "delivered". The old fire-and-forget claimed
        vision at schedule time: a session that dropped in
        between swallowed the frame silently and the tool
        result still said the model had seen the screen. Any
        other status — dropped, failed, degraded to OCR,
        unconfirmed — raises into the honest path below, so
        the tool result carries the text wording instead.

        Every other brain is text-only from the model's side of
        this call, so instead of starting a competing image turn
        the frame is read here, synchronously, through the local
        session's describe seam (OCR + window title, the OCR
        enveloped as untrusted screen text) and the resulting text
        is returned for the tool result to carry. Raises when no
        session is connected — tools turn that into the honest
        "no active session can view images" result."""
        worker = getattr(self, "worker", None)
        loop = getattr(worker, "loop", None) if worker is not None else None
        if not (loop and live_session.is_connected):
            raise RuntimeError("no active session can receive frames")
        # v5.45 (H12): from here a frame WILL reach a brain — note it
        # as untrusted image content for the injection taint (the
        # tools_handlers.py sink covers its own path; this closes the GUI one).
        try:
            from voice_assistant.core.gui_facade import note_image_frame
            note_image_frame()
        except Exception:  # noqa: BLE001 — bookkeeping never breaks delivery
            pass
        engine = str(getattr(live_session, "_engine", "") or "").lower()
        inner = getattr(live_session, "_inner", None)
        # GAPS-22 (B-V3): a Gemini session is identified by its
        # engine attribute, never by its class name. (Compared by
        # value here — Engine is a str enum, so "gemini" matches
        # Engine.GEMINI; the type itself is not imported in the GUI.)
        native_vision = engine == "gemini" or (
            inner is not None
            and getattr(inner, "engine", None) == "gemini")
        # EYES E5a (v5.45): a session that declares native frame
        # vision (the Groq brain — GroqSession.native_frame_vision)
        # also takes this branch. Since v5.53 (F8) the branch
        # only chooses the TRANSPORT: the sink's "vision"
        # answer comes from the send's delivery status, so a
        # Groq turn that degrades to OCR internally reports
        # "degraded" and the sink does NOT call it vision —
        # the frame produced a real turn, but a text one.
        if not native_vision and inner is not None and bool(
                getattr(inner, "native_frame_vision", False)):
            native_vision = True
        if native_vision:
            future = asyncio.run_coroutine_threadsafe(
                live_session.send_image_frame(image_bytes, mime_type), loop)
            status = _await_frame_delivery(future, loop)
            if status == "delivered":
                return None
            # v5.53 (F8): anything else — the session dropped
            # between the check above and the send
            # ("no-session"), the send failed ("error"), the
            # vision turn degraded to the OCR describe
            # ("degraded": the model answered from text), or
            # the outcome could not be confirmed inside the
            # bounded wait — is NOT vision. Raise into the
            # honest path: the tool result takes the same
            # wording a text session gets, never "the frame
            # was shown to the model". The delivery itself is
            # already on the record — the session logs both
            # outcomes (core/voice/live_client.py, core/voice/cloud_text_client.py).
            raise RuntimeError(
                f"frame delivery not confirmed ({status})")
        from voice_assistant.core.gui_facade import LocalLiveSession  # Phase 3 V1b: moved to core/voice/

        return LocalLiveSession._describe_frame(image_bytes)
