from __future__ import annotations

import asyncio

try:
    from google import genai
    from google.genai import types
except Exception:  # noqa: BLE001 — a build without google-genai must
    # still import: the local brain keeps working, and cloud paths
    # report the missing library honestly instead of crashing.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

from .. import injection
from ..memory.memory import memory_store
from ..taskwake import cancel_waking

# INTENTIONAL (skills split B47): send/turn-surface mixin. Each sibling
# import is exactly a name its methods resolve by bare name (B45 P3
# lesson, per-mixin): resume/helpers families + the B46 error-cost
# names (_log_line — send_image_frame resolves it — and _short_exc
# for the field-check fix #2 loss note in send_text).
from .live_client_error_cost import _log_line, _short_exc
from .live_client_helpers import _bump_epoch
from .live_client_resume import _ambient_packet_text, _clear_resume_handle


class GeminiLiveSessionSendMixin:
    # INTENTIONAL (skills split B47): send/turn surface — exhaustion
    # reporting (terminal status report, called by start through MRO),
    # client property, ambient-context refresh/push, send_text,
    # send_image_frame, run_plan, run_goal, speech-speed override,
    # stop. Cross-mixin self-calls (run_goal -> run_plan is internal;
    # stop -> resume helpers are imports above) resolve through MRO.


    def _report_exhaustion(self, candidates, timed_out, last_rejection):
        """No candidate connected: report what ACTUALLY happened
        (v5.28 wording, moved verbatim in v5.29). The GUI maps
        MODEL_REJECTED for its presentation, and the pure timeout
        text for a path-wide stall — a mixed outcome carries the
        MODEL_REJECTED prefix with the timeouts named inside, so
        neither half of the story is swallowed.

        Fix 5: the last rejection is classified through
        cloud_diag.classify_live_error, so a quota refusal is
        named LIVE_QUOTA (not MODEL_REJECTED), and Google's exact
        reply rides in the status verbatim, capped at 300 chars
        (parity with the connect ERROR cap) instead of 180 — the
        status IS the tooltip, so the 180 cap was swallowing the
        evidence the user needs. The full text still lands in
        data/activity.log via _log_line, as before."""
        from .cloud_diag import classify_live_error

        classified = (classify_live_error(last_rejection)
                      if last_rejection is not None else None)
        kind = classified["kind"] if classified else "unknown"
        verbatim = classified["verbatim"] if classified else ""
        if timed_out and last_rejection is None:
            # Every candidate stalled: only NOW is the network path
            # the honest suspect (the v5.22 inference, kept for the
            # case it fits).
            self._notify_status(
                "ERROR: Timed out reaching Google's voice "
                "service (45s connect budget, 25s for the first "
                f"model then 10s each; tried: "
                f"{', '.join(candidates)}). This usually means "
                "the network path is blocked — on campus or "
                "office networks the system proxy is the usual "
                "suspect; VYUK's Diagnose downloads "
                "output shows the detected proxy.")
            _log_line("All voice model candidates timed out; "
                      f"tried: {', '.join(candidates)}")
        elif timed_out:
            if kind == "quota":
                # Fix 5: a quota refusal is NOT a model rejection
                # — naming it MODEL_REJECTED sent users hunting
                # the model list / key when the fix is waiting
                # (or billing). The kind prefix is what
                # _friendly_error and _is_fatal_error key on.
                self._notify_status(
                    "ERROR: LIVE_QUOTA: Google refused the voice "
                    "models with a quota / rate limit "
                    f"({', '.join(candidates)}). The key has hit "
                    "its Live usage limit for now — wait, or turn "
                    "on billing for the key's project in Google AI "
                    "Studio. Google's exact reply: "
                    f"{verbatim}")
            else:
                self._notify_status(
                    "ERROR: MODEL_REJECTED: Google refused or "
                    "never answered every voice model VYUK "
                    f"tried ({', '.join(candidates)}). Timed out "
                    f"after 25s: {', '.join(timed_out)}. Either "
                    "this build's model list is out of date, or "
                    "your API key can't use Gemini Live models — "
                    "a key from a project without the Gemini API "
                    "enabled fails exactly like this. Google's "
                    "exact reply: "
                    f"{verbatim}")
            _log_line("Voice model candidates exhausted: "
                      f"timed out: {', '.join(timed_out)}; "
                      f"last rejection: {last_rejection}")
        else:
            # Every candidate was rejected as a model problem: say
            # so plainly, with BOTH honest causes named: the list
            # may be stale, but a key whose project never enabled
            # the Gemini API fails in exactly the same way, and
            # blaming only the list sends users hunting the
            # wrong fix.
            if kind == "quota":
                self._notify_status(
                    "ERROR: LIVE_QUOTA: Google refused every voice "
                    f"model VYUK tried ({', '.join(candidates)}) "
                    "with a quota / rate limit. The key has hit "
                    "its Live usage limit for now — wait, or turn "
                    "on billing for the key's project in Google AI "
                    "Studio. Google's exact reply: "
                    f"{verbatim}")
            else:
                self._notify_status(
                    "ERROR: MODEL_REJECTED: Google refused every voice "
                    f"model VYUK tried ({', '.join(candidates)}). "
                    "Either this build's model list is out of date, or "
                    "your API key can't use Gemini Live models — a key "
                    "from a project without the Gemini API enabled fails "
                    "exactly like this. Google's exact reply: "
                    f"{verbatim}")
            _log_line(f"All voice model candidates rejected; last error: {last_rejection}")

    @property
    def client(self) -> genai.Client | None:
        """Returns the underlying genai.Client for secondary tasks like summaries."""
        return self._client

    def refresh_ambient_context(self, packet: str | None = None):
        """N4 refresh (v5.44): forward a materially-changed ambient
        packet as a SILENT context message — the watcher-notice
        injection transport (send_client_content), but bracketed
        as background context: it is not spoken, not echoed to the
        transcript, not logged as a user line, and (bracketed) it
        never lifts the injection taint.

        SYNCHRONOUS by contract: the caller (BrainSession, driven
        by the GUI's watcher path) calls this from a non-async
        thread, so the actual send is scheduled onto the session's
        own event loop (captured in _run_session). No-op when the
        packet is empty/unchanged, when disconnected, or when no
        live loop is available to carry the send."""
        if packet is None:
            packet = _ambient_packet_text()
        if not packet or packet == self._ambient_packet:
            return
        self._ambient_packet = packet
        if not (self._session and self.is_connected):
            return
        loop = getattr(self, "_event_loop", None)
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(
                self._push_ambient_context(packet), loop)
        except RuntimeError:
            pass  # loop already gone — the session is ending anyway

    async def _push_ambient_context(self, packet: str):
        """The async half of the N4 refresh: one bracketed
        client-content turn carrying the packet as background
        context (see refresh_ambient_context)."""
        text = ("[Ambient context update — background context only; "
                "do not reply to this or mention it unless it "
                f"becomes relevant: {packet}]")
        try:
            content = types.Content(
                parts=[types.Part.from_text(text=text)], role="user")
            send_content = getattr(self._session,
                                   "send_client_content", None)
            if callable(send_content):
                await send_content(turns=content, turn_complete=True)
            else:
                await self._session.send(input=content, end_of_turn=True)
        except Exception as e:  # noqa: BLE001 — context is a nicety
            print(f"[Live] Ambient context update failed: {e}")

    async def send_text(self, text: str):
        """Sends manual typed user text to the live session."""
        # Chatfix2 diagnostic: echo-execution flag for the typed
        # send diag line (see gui/window_session._on_typed_send_done).
        # Reset here so a silent no-op (session guard below, or a
        # proxy-level no-op) cannot inherit a stale True from an
        # earlier send; set True only when the echo call below
        # actually executes. Diagnostic only — no send, render,
        # or modality behaviour changes.
        self._diag_typed_echo_ran = False
        # Genuine typed user text lifts the injection taint (v5.3);
        # bracketed VYUK-internal injections (watcher notices arrive
        # through this same method) do not.
        if not (text or "").lstrip().startswith("["):
            injection.clear_taint()
            # v5.52 (item 7): publish the turn's instruction for
            # the executor's echo guard (core/brain/turn_context).
            # Typed turns only — Live voice turns are transcribed
            # server-side and never surface text here, so for
            # those the guard simply has no instruction to judge
            # against and stays out of the way.
            try:
                from ..brain import turn_context as _tc  # Phase 3 V1b: moved to core/voice/

                _tc.set_current_instruction(text)
            except Exception:  # noqa: BLE001 — never break a turn
                pass
        if self._session and self.is_connected:
            content = types.Content(
                parts=[types.Part.from_text(text=text)],
                role="user"
            )
            # v5.32: the documented send_client_content; the
            # deprecated unified send() is the fallback.
            send_content = getattr(self._session,
                                   "send_client_content", None)
            if callable(send_content):
                await send_content(turns=content, turn_complete=True)
            else:
                await self._session.send(input=content, end_of_turn=True)
            if self.on_transcript:
                self.on_transcript("user", text)
                self._diag_typed_echo_ran = True
            try:
                memory_store.log_message("user", text)
            except Exception as exc:  # noqa: BLE001 - field-check
                # fix #2 (T7 #16): a memory-write failure must
                # never fail a typed turn — the text was already
                # sent, so the turn stands and the lost row is
                # surfaced instead of swallowed
                _log_line(
                    "The typed message was sent but could not be "
                    "saved to memory: %s" % _short_exc(exc))

    async def send_image_frame(self, image_bytes: bytes, mime_type: str = "image/jpeg"):
        """Sends a visual frame (screen or webcam) to the live session.

        Returns a delivery status (v5.53, F8): "delivered" when
        the frame was handed to the session's realtime input,
        "no-session" when no live session is up, "error" when
        the send itself failed. Before F8 this method returned
        None in every case and no-opped SILENTLY on a dropped
        session, so callers claimed the model had seen frames
        that never arrived; both outcomes are logged now, and
        the frame sink (gui/window_session) honours only
        "delivered" as vision."""
        if not (self._session and self.is_connected):
            _log_line(
                "Frame delivery skipped: no live session — "
                "the frame was not shown to the model.")
            return "no-session"
        try:
            # v5.32: send_realtime_input(video=...), as documented;
            # the legacy media_chunks send() is the fallback.
            send_realtime = getattr(self._session,
                                    "send_realtime_input", None)
            if callable(send_realtime):
                # E10 remainder (v5.43): the frame's true capture
                # geometry rides as a companion realtime TEXT input
                # immediately before the frame — the same seam the
                # audio/video stream itself uses, one argument per
                # call, no client-content turn and no turn_complete,
                # so turn order is undisturbed and the model holds
                # the coordinate contract when the pixels arrive.
                # Frames delivered here are screen captures whose
                # capture just recorded its geometry (the Share
                # button, the tool frame sink); with no geometry on
                # record, only the frame is sent, as before. The
                # legacy send() fallback has no such seam — it is
                # left exactly as it was.
                geo_line = ""
                try:
                    from ..desktop.vision import capture_geometry_line  # Phase 3 V1b: moved to core/voice/ (D4c: vision hub moved to core/desktop/)

                    geo_line = capture_geometry_line()
                except Exception:  # noqa: BLE001 — geometry is
                    geo_line = ""  # best-effort; the frame matters
                if geo_line:
                    try:
                        await send_realtime(text=geo_line)
                    except Exception:  # noqa: BLE001 — a failed
                        pass  # companion never sinks the frame
                await send_realtime(video=types.Blob(
                    data=image_bytes, mime_type=mime_type))
            else:
                realtime_input = types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(data=image_bytes, mime_type=mime_type)
                    ]
                )
                await self._session.send(input=realtime_input)
        except Exception as e:  # noqa: BLE001 — a failed send is
            # a status, not a crash (v5.53, F8)
            _log_line(f"Frame delivery failed: {e}")
            return "error"
        _log_line(
            f"Frame delivered to the live session "
            f"({len(image_bytes)} bytes, {mime_type}).")
        # Live staleness stage, P1 (2026-11-05): the delivery
        # ledger records ONLY genuinely delivered frames — this
        # is the "last frame the model saw" baseline, separate
        # from the vision slot (which fills at capture time even
        # for frames that never deliver). Best-effort and never
        # load-bearing: a ledger failure must not change the
        # delivery status this method just determined.
        try:
            from ..desktop import live_staleness as _ls
            from ..desktop.vision import last_capture_info
            _ls.record_delivery(image_bytes,
                                capture_record=last_capture_info())
        except Exception:  # noqa: BLE001 — ledger failure never
            pass  # sinks a delivery that already happened
        return "delivered"

    async def run_plan(self, plan_id: str) -> str:
        """B15 driver entry (v5.45): walk a plan through
        core.brain.planner.run_plan. The Live brain is model-paced
        audio with no text chat seam, so the picker can only
        honour steps that DECLARE their tool (planner.run_plan
        runs those directly); an undeclared step halts the walk
        with that honest note rather than guessing. Progress
        lines go to the transcript."""
        from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/
        from ..desktop import uia as _uia  # Phase 3 V1b: moved to core/voice/; Phase 3 D4b: uia hub moved to core/desktop/

        async def _pick(step, plan):
            return None

        def _emit(line: str):
            if self.on_transcript:
                self.on_transcript("assistant", line)

        # v5.49 (programme item 31): declared steps get expect
        # verdicts too — same passthrough as the text brains.
        return await _planner.run_plan(
            plan_id, pick_tool=_pick, emit=_emit,
            verify_expect=_uia.verify_expectation)

    async def run_goal(self, goal_text: str) -> str:
        """Goal entry (v5.49, programme item 29) — the HONEST
        counterpart to the text brains' run_goal, not a fake one.

        The Live brain is model-paced audio with no text picker
        seam, so a free-form goal loop cannot be driven from
        here (the same B15 limit run_plan documents): no picker
        is invented and no action is ever guessed. Two honest
        paths exist instead:
          * the goal text names a saved plan (by id, e.g.
            "plan-2", or by the plan's goal text) → delegate to
            this session's run_plan, which walks that plan's
            DECLARED steps;
          * otherwise → return the plain explanation below and
            offer the declared-steps path.
        Wave 1 bridge (core/voice/voice_text_bridge.py, explicit flag
        default OFF): when the bridge is enabled AND a text brain
        is injected, a free-form goal is handed to that text
        brain's run_goal and the report is narrated back through
        on_transcript. When the bridge is OFF or no text brain
        is available, this method behaves exactly as before —
        the refusal below is unchanged. The bridge takes no
        consequential action itself; every loop action passes
        planner.run_goal's gate stack (C-14 included).

        Voice-brain adoption: a bridged goal additionally runs as
        a coordinator TASK (core/brain/voice_goal.py) — task id,
        lifecycle, ledger record, receipt, narrated summary — so
        a voice goal is no longer invisible to the task system.
        Cancellation feeds the loop's interruption check
        (coordinator cancel flag or session stop halts it at the
        next step boundary); the loop's completion markers decide
        completed vs failed, fail-closed. The voice brain itself
        still has no native picker loop — planning comes from the
        injected text brain, and the capability contract says so
        (free-form YES-when-bridge-available, NO otherwise)."""
        from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/

        text = str(goal_text or "").strip()
        low = text.lower()
        matched_id = None
        try:
            plans, _damaged = _planner._load_plans()
        except Exception:  # noqa: BLE001 — no readable store, no match
            plans = []
        for plan in plans or []:
            plan_id = str(plan.get("id") or "").strip()
            plan_goal = str(plan.get("goal") or "").strip()
            if plan_id and plan_id.lower() in low:
                matched_id = plan_id
                break
            if plan_goal and plan_goal.lower() in low:
                matched_id = plan_id
                break
        if matched_id:
            note = (f"Running saved plan {matched_id}: the Live "
                    "session can walk a plan's declared steps, so "
                    "the named plan runs here.")
            if self.on_transcript:
                self.on_transcript("assistant", note)
            report = await self.run_plan(matched_id)
            return f"{note}\n{report}"
        # Wave 1 bridge, owned by the coordinator (voice-brain
        # adoption): a bridged goal runs as a task with ledger,
        # receipt, and cancellation. When the bridge cannot run,
        # run_bridged_voice_goal returns None and the refusal
        # below is byte-for-byte the pre-bridge refusal.
        try:
            from ..brain import voice_goal as _vg  # Phase 3 V1b: moved to core/voice/
            bridged = await _vg.run_bridged_voice_goal(self, text)
        except Exception:  # noqa: BLE001 — bridge failure is a refusal
            bridged = None
        if bridged is not None:
            return bridged
        message = (
            "I can't run a free-form goal loop on the Live brain: "
            "this session is model-paced audio with no text "
            "picker to drive one action at a time, so nothing was "
            "run. Free-form goal loops run on the text brains "
            "(the local brain or Groq) — switch brain and ask "
            "again there. Or save this goal as a plan whose steps "
            "declare their tools, and I can run that plan here "
            "with run_plan.")
        if self.on_transcript:
            self.on_transcript("assistant", message)
        return message

    def set_speech_speed_override(self, factor):
        """v5.17 (Mouth M18): accepted for session-surface parity,
        deliberately a no-op — the Gemini Live voice is synthesized
        by Google at its own pace; there is no local speech chain
        here for an override to act on."""
        return None

    def stop(self):
        self._running = False
        # Voice-control batch (2026-11-16, B3): a stopped session
        # is gone — clear the reference here as well as in
        # start()'s finally, so no path can mistake it for alive.
        self._session = None
        _bump_epoch("live stop")  # v5.48 (programme item 20)
        # GAPS-22 (B-V5): nothing is serving after a stop — the
        # connected model clears with the connection. (The module
        # singleton itself is borrowed, not destroyed; the next
        # start sets the model again at connect.)
        self.connected_model = None
        # B9 (v5.45): a deliberate stop is a CLEAN close — the
        # saved resumption handle is cleared, so the next session
        # starts cold by the user's own choice. (An unexpected
        # drop never calls stop(), so its handle survives for
        # the reconnect.)
        _clear_resume_handle()
        # v5.32: cancel via the loop-waking helper — a bare
        # cancel() from this (GUI) thread does not reliably wake
        # the worker loop to deliver it (core/taskwake.py).
        cancel_waking(self._send_task)
        cancel_waking(self._receive_task)
        # H2 (v5.44): the tool worker dies with the session; a
        # batch in flight abandons its delivery (see
        # _run_tool_batch) rather than landing in the next session.
        cancel_waking(self._tool_worker_task)
        # P2 speed pass: the filler watchdog dies with the
        # session too — a ticking bridge must never speak into
        # the next session (cancel_waking is None-safe).
        cancel_waking(getattr(self, "_filler_task", None))
