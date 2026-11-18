# Batch 3 (plan §3.3): mic-utterance, moved verbatim
# from core/voice/local_client.py. INTENTIONAL: imports below are the
# subset of the old module's import block that this group's code uses.
import asyncio
import time
import numpy as np
from ..config import config
from .. import failsafe, injection
from ..memory.memory import memory_store
from . import local_assets
from .local_client_guards import (
    _is_lite_tier_model, _transcription_settings,
    context_pressure_from_history, prior_turn_failed_from_trace)
from .local_client_classifier import classify_turn_profile
from .local_client_render import (
    _is_probable_hallucination, _pre_roll_before_onset, _ring_pre_roll_pcm)
from .local_client_vad import NoiseFloorEstimator
from .mic_loop import chunk_rms as _shared_chunk_rms, run_mic_loop
from .smartturn import (  # GAPS-25 (V-2): turn-detector consultation
    consult_turn_detector as _consult_turn_detector)


class LocalLiveSessionMicMixin:

    # ------------------------------------------------------------------ #
    # Mic loop + energy VAD
    # ------------------------------------------------------------------ #
    @staticmethod
    def _chunk_rms(pcm: bytes) -> float:
        # GAPS-16: the one RMS lives in core/voice/mic_loop.py; this
        # static stays so existing call sites (the hallucination
        # filter below, the lite session's import) keep working.
        return _shared_chunk_rms(pcm)

    @staticmethod
    def _max_chunk_rms(pcm: bytes) -> float:
        """The LOUDEST 64 ms window's RMS in an utterance (voice-
        control batch, 2026-11-16, V9): the hallucination gate
        judges "was anything here loud", so the max window is
        the honest statistic — the whole-utterance mean diluted
        a loud word inside a mostly-silent utterance below the
        gate and dropped genuine speech as a hallucination."""
        try:
            samples = np.frombuffer(pcm, dtype=np.int16)
        except Exception:  # noqa: BLE001 — garbage PCM reads as silence
            return 0.0
        if samples.size == 0:
            return 0.0
        window = 1024  # 64 ms at 16 kHz
        peak = 0.0
        for start in range(0, samples.size, window):
            chunk = samples[start:start + window].astype(np.float64)
            if chunk.size == 0:
                continue
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            if rms > peak:
                peak = rms
        return peak

    def _listen_timed_out(self) -> bool:
        """True when the listen timeout (v5.17, A15) has elapsed:
        config.listen_timeout_s > 0 and that many seconds have
        passed with no completed utterance, no turn in flight, and
        no speech playing. The activity clock is the latest of the
        last utterance dispatch and the brain/STT activity stamps,
        so a typed conversation (send_text) also holds the session
        open — only genuine silence counts. A timeout of 0 (the
        default) disables the feature entirely."""
        try:
            timeout = float(getattr(config, "listen_timeout_s", 0.0))
        except (TypeError, ValueError):
            return False
        if timeout != timeout or timeout <= 0:  # NaN or disabled
            return False
        if self._speaking or self._turn_tasks:
            return False
        last = max(self._last_utterance_at,
                   self._last_brain_activity,
                   self._last_stt_activity)
        return (time.monotonic() - last) >= timeout

    async def _mic_loop(self, audio_manager):
        """The local engine's mic loop — now a thin wrapper over the
        one shared loop (core/voice/mic_loop.run_mic_loop). The
        state machine lives there; this method only supplies the
        local engine's hooks: its persisted noise-floor estimator
        (the floor — and the doctor's snapshot of it — survives
        across loop runs), its pre-roll (v5.43 A2), its listen
        timeout (v5.17 A15), its faded interrupt, and its
        task-per-utterance dispatch."""
        estimator = getattr(self, "_vad_estimator", None)
        if estimator is None:
            estimator = self._vad_estimator = NoiseFloorEstimator()
        await run_mic_loop(
            audio_manager,
            running=lambda: self._running,
            is_speaking=lambda: self._speaking,
            set_speaking=lambda value: setattr(self, "_speaking", value),
            interrupt_playback=self._interrupt_playback,
            on_utterance=self._dispatch_utterance,
            estimator=estimator,
            pre_roll=(_pre_roll_before_onset, _ring_pre_roll_pcm),
            should_stop=self._listen_timeout_hook,
        )

    async def _dispatch_utterance(self, pcm: bytes,
                                  trailing_silence_s: float = 0.0
                                  ) -> None:
        """One finished utterance: stamp it and dispatch it as its
        own task (the shared loop awaits this, so the loop never
        blocks on a turn). GAPS-25 (V-2): the trailing silence
        that ended the utterance travels with it to
        _process_utterance, which hands it to the turn detector's
        consultation."""
        self._last_utterance_at = time.monotonic()
        task = asyncio.create_task(
            self._process_utterance(pcm, trailing_silence_s))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    def _listen_timeout_hook(self) -> bool:
        """The listen-timeout stop hook for the shared loop (v5.17,
        A15): a silent stretch this long ends the session, which
        is this module's only "outer wait" — start() returns,
        DISCONNECTED is emitted by _run_session, and the product
        drops back to wake-waiting at the GUI layer. Cancelling
        the sibling session task (housekeeping) is what lets
        _run_session's gather complete. Returns True when the
        session must stop."""
        if not self._listen_timed_out():
            return False
        try:
            timeout = float(getattr(config, "listen_timeout_s", 0.0))
        except (TypeError, ValueError):
            timeout = 0.0
        print(f"[LocalBrain] No completed utterance for "
              f"{timeout:g}s (listen timeout) — returning "
              "to wake-waiting.")
        self._running = False
        for task in self._session_tasks:
            if task is not asyncio.current_task():
                task.cancel()
        return True

    # ------------------------------------------------------------------ #
    # One voice turn: STT → brain → speech
    # ------------------------------------------------------------------ #
    async def _handle_cancel_all(self, text: str) -> bool:
        """Pre-model cancel-all check (v5.42, H14): a BARE stop
        utterance (core/failsafe.is_cancel_all_utterance — "stop",
        "vyuk stop", "cancel everything", "enough", and nothing
        else) never reaches the brain. The in-flight playback is
        cut, pending speech is drained, and a short "Stopped."
        confirmation is emitted + spoken instead. Returns True when
        the utterance was a cancel. Fail-safe: any error in the
        predicate reads as a CANCEL attempt (field-check fix #16,
        T8 #25) — a broken check must never turn "stop everything"
        into a normal turn; the failure is logged and the stop
        proceeds. Once matched, the turn ends here whatever the
        speech path does."""
        try:
            if not failsafe.is_cancel_all_utterance(text):
                return False
        except Exception as exc:  # noqa: BLE001
            # A predicate error used to read as "not a cancel",
            # so "stop everything" became a normal turn with no
            # log and no hint. Fail closed toward the stop: the
            # user said stop, and the check that would clear it
            # is broken.
            print(f"[LocalBrain] cancel-all check failed "
                  f"({exc!r}) — treating the utterance as a stop.")
        try:
            self._interrupt_playback()
        except Exception:  # noqa: BLE001 — the cut is best-effort
            pass
        self._speaking = False
        try:
            self._drain_speech_queue()
        except Exception:  # noqa: BLE001
            pass
        if self.on_transcript:
            self.on_transcript("assistant", "Stopped.")
        memory_store.log_message("assistant", "Stopped.")
        try:
            await self._speak("Stopped.")
        except Exception:  # noqa: BLE001 — confirmation speech is degradable
            pass
        self._notify_status("LISTENING")
        return True

    async def _process_utterance(self, pcm_bytes: bytes,
                                 trailing_silence_s: float = 0.0):
        # RET-1: the whole turn is a voice turn — funnel successes
        # inside it write the voice_task marker (core/voice_origin.py).
        from ..voice_origin import voice_turn

        with voice_turn():
            return await self._process_utterance_turn(
                pcm_bytes, trailing_silence_s)

    async def _process_utterance_turn(self, pcm_bytes: bytes,
                                      trailing_silence_s: float = 0.0):
        self._notify_status("THINKING")
        async with self._turn_lock:
            loop = asyncio.get_running_loop()
            text = await loop.run_in_executor(None, self._transcribe, pcm_bytes)
            if not text:
                self._notify_status("LISTENING")
                return
            # Whisper hallucination filter (v5.19): a stock phrase
            # "heard" in a near-silent utterance is the recogniser
            # dreaming, not the user speaking — drop the turn before
            # it is echoed, logged, or answered.
            if _is_probable_hallucination(
                    text, self._max_chunk_rms(pcm_bytes)):
                print(f"[LocalBrain] Dropped probable Whisper "
                      f"hallucination: {text!r}")
                self._notify_status("LISTENING")
                return
            # GAPS-25 (V-2): consult the turn detector on the
            # finished turn — audio, transcript and the trailing
            # silence that ended it. Observation only: the verdict
            # is recorded on the session and never gates dispatch;
            # the turn proceeds exactly as before.
            _consult_turn_detector(
                self, pcm_bytes, text, trailing_silence_s)
            # A genuine user message: the user is driving again, so
            # any injection taint from earlier content lifts (v5.3).
            injection.clear_taint()
            if self.on_transcript:
                self.on_transcript("user", text)
            memory_store.log_message("user", text)
            # v5.42 (H14): a bare cancel-all utterance is handled
            # pre-model — it never becomes a brain turn.
            if await self._handle_cancel_all(text):
                return
            await self._brain_turn(text)

    def _transcribe(self, pcm_bytes: bytes) -> str:
        """Sync faster-whisper transcription (runs in an executor).

        Returns "" when the model can't load — the session then degrades
        to typed input instead of dying on a missing optional extra.
        Voice-control batch (2026-11-16, V5): one shared STT call —
        asr.transcribe_text with _transcription_settings() as the
        single kwargs source — so the locked language and biasing
        prompt apply here exactly as in meetings, and a non-16 kHz
        buffer errors instead of transcribing as garbage.
        """
        self._last_stt_activity = time.monotonic()
        if not self._ensure_whisper():
            return ""
        try:
            from .asr import transcribe_text as _transcribe_text

            return _transcribe_text(
                pcm_bytes, word_timestamps=False,
                **_transcription_settings())
        except Exception as e:  # noqa: BLE001
            print(f"[LocalBrain] Transcription failed: {e}")
            return ""
        finally:
            self._last_stt_activity = time.monotonic()

    async def _brain_turn(self, user_text: str):
        """Dispatches one brain turn to the active backend. Callers own
        the transcript/log echo of the user's line; the backend method
        owns history, the assistant transcript, and speech."""
        # v5.52 (item 7): publish the turn's instruction for the
        # executor's echo guard (core/brain/turn_context) — one site
        # covers both backends, since both dispatch from here.
        try:
            from ..brain import turn_context as _tc  # Phase 3 V1b: moved to core/voice/

            _tc.set_current_instruction(user_text)
        except Exception:  # noqa: BLE001 — never break a turn
            pass
        self._last_brain_activity = time.monotonic()
        # SOCIAL Phase 1 (approved design, WhatsApp first): while a
        # WhatsApp send is open, "press Enter" belongs to Windows
        # Voice Access, not to VYUK — both systems listen, so the
        # phrase is consumed here as the owner's send confirmation
        # and never dispatched as a command. Best-effort and never
        # turn-breaking: if the check itself fails, the turn goes
        # through normally.
        try:
            from ..social_send import (
                note_press_enter_heard as _note_press_enter_heard,
                should_ignore_utterance as _should_ignore_utterance,
            )
            if _should_ignore_utterance(user_text):
                _note_press_enter_heard()
                return
        except Exception:  # noqa: BLE001 — the check never breaks a turn
            pass
        # B13 (v5.45) + 7th-B09: this turn's sampling profile,
        # from measured turn-start features — utterance shape
        # plus whether the previous turn failed and how full the
        # context already is (both best-effort, never turn-breaking).
        try:
            _turn_budget = local_assets.effective_n_ctx() - 512 - 128
        except Exception:  # noqa: BLE001 — pressure reads as 0.0
            _turn_budget = 0
        self._last_profile_escalations: tuple[str, ...] = ()
        _prior_failed = prior_turn_failed_from_trace(
            getattr(self, "_turn_action_trace", None))
        self._active_profile = classify_turn_profile(
            user_text,
            prior_turn_failed=_prior_failed,
            context_pressure=context_pressure_from_history(
                getattr(self, "_history", None), _turn_budget))
        # GAPS-6 (BLOCKS #5 routing half): this turn's model tier,
        # from the query itself — one routing decision per turn,
        # next to the profile decision above. Best-effort and never
        # turn-breaking: a routing failure falls back to the
        # effective tier. The resident GGUF does not swap mid-turn;
        # a routed tier that differs takes effect at the next load.
        try:
            from ..config import DATA_DIR as _DATA_DIR
            from ..query_routing import (
                last_turn_outcome as _last_turn_outcome,
                route_for_turn as _route_for_turn)
            _eff = local_assets.effective_model() or {}
            # GAPS-22 (B-V11): the loop half — the next decision
            # sees the previous turn's outcome (as hints) and the
            # cost ledger (as a snapshot), both best-effort.
            _last_outcome = _last_turn_outcome() or {}
            _hints = {"prior_turn_failed": _prior_failed,
                      "tool_calls": int(_last_outcome.get("tool_calls") or 0),
                      "repair_rounds": int(_last_outcome.get("repairs") or 0)}
            _ledger_snapshot = None
            try:
                from ..cost_ledger import CostLedger as _CostLedger
                _led = _CostLedger.from_env()
                try:
                    _summary = _led.summary()
                    _preflight = _led.preflight(
                        "live", config.model_name or "gemini", 0, 0)
                    _ledger_snapshot = {
                        "spend_today_usd": _summary.get("spend_today_usd"),
                        "daily_cap_usd": _summary.get("daily_cap_usd"),
                        "degraded": bool(_preflight.get("degraded"))}
                finally:
                    _led.close()
            except Exception:  # noqa: BLE001 — no snapshot, no loop input
                _ledger_snapshot = None
            self._active_routing = _route_for_turn(
                user_text,
                effective_tier=_eff.get("tier"),
                enabled=bool(getattr(config, "query_routing", True)),
                user_tier=(_eff.get("tier")
                           if _eff.get("source") == "your pick" else None),
                data_dir=str(_DATA_DIR),
                hints=_hints,
                ledger=_ledger_snapshot)
        except Exception:  # noqa: BLE001 — routing never breaks a turn
            self._active_routing = None
        _trace_mark = len(getattr(self, "_turn_action_trace", None) or [])
        _turn_started = time.monotonic()
        if self._backend == "ollama":
            await self._brain_turn_ollama(user_text)
        else:
            await self._brain_turn_builtin(user_text)
        # GAPS-22 (B-V11): close the routing loop — record what
        # this turn actually did against the decision above, so
        # the next turn's routing learns from it. "Failed" is the
        # same evidence prior_turn_failed_from_trace reads (an
        # action with status failed/rejected, in an entry this
        # turn appended); a turn that recorded no actions reads
        # as ok — never assume trouble without evidence.
        # Best-effort and never turn-breaking.
        try:
            from ..query_routing import (
                record_turn_outcome as _record_turn_outcome)
            _trace = getattr(self, "_turn_action_trace", None) or []
            _new_entries = _trace[_trace_mark:]
            _turn_ok = not any(
                (a or {}).get("status") in ("failed", "rejected")
                for _entry in _new_entries
                for a in ((_entry or {}).get("actions") or []))
            _turn_tools = sum(
                len((_entry or {}).get("actions") or [])
                for _entry in _new_entries)
            _record_turn_outcome(
                getattr(self, "_active_routing", None),
                ok=_turn_ok,
                repairs=len(
                    getattr(self, "_last_profile_escalations", ()) or ()),
                tool_calls=_turn_tools,
                latency_ms=round(
                    (time.monotonic() - _turn_started) * 1000.0, 1))
        except Exception:  # noqa: BLE001 — recording never breaks a turn
            pass
        self._last_brain_activity = time.monotonic()
        await self._maybe_auto_summarize()

    async def _maybe_auto_summarize(self):
        """Turn-count summary trigger (v5.45, B5): every N turns
        (session_summary.TurnSummaryTrigger) the conversation so
        far is summarised with THIS brain and stored as an
        episode. Skipped entirely on the Lite tier. Best-effort:
        a summary failure never touches the turn that tripped it."""
        try:
            from ..memory import session_summary as _ss  # Phase 3 V1b: moved to core/voice/

            if _ss.is_lite_tier():
                return
            trigger = getattr(self, "_summary_trigger", None)
            if trigger is None:
                trigger = _ss.TurnSummaryTrigger()
                self._summary_trigger = trigger
            if not trigger.note_turn():
                return
            trigger.mark_fired()
            transcript = [dict(m) for m in getattr(self, "_history", [])]
            if len(transcript) < 4:
                return
            loop = asyncio.get_running_loop()

            if self._backend == "ollama":
                def _summarise(prompt: str) -> str:
                    reply = self._ollama_chat(
                        [{"role": "user", "content": prompt}], [])
                    msg = (reply or {}).get("message") or {}
                    return str(msg.get("content") or "")
            else:
                if getattr(self, "_llama", None) is None:
                    return

                def _summarise(prompt: str) -> str:
                    return str(self._llama_chat(
                        [{"role": "user", "content": prompt}]) or "")

            # The blocking chat runs in an executor; generate_
            # session_summary awaits the async summariser itself.
            async def _async_summarise(prompt: str) -> str:
                return await loop.run_in_executor(None, _summarise, prompt)

            await _ss.generate_session_summary(
                transcript, summariser=_async_summarise)
        except Exception:  # noqa: BLE001 — summaries are a nicety
            pass

    def _default_confirm_lite_plan(self, warning: str) -> bool:
        """The conservative default for confirm_lite_plan
        (v5.49, programme item 36): ask through
        core.confirm_gate — the session family's existing human
        confirmation seam — when the GUI has registered a
        handler; with no handler (or any failure) the answer is
        NO. A multi-step run on the small brain is never silently
        approved. Sync and potentially blocking (the gate waits
        for the human), so the async gate below calls it in an
        executor."""
        try:
            from .. import confirm_gate  # Phase 3 V1b: moved to core/voice/

            if not confirm_gate.has_handler():
                return False
            return bool(confirm_gate.request_confirmation(
                "multi-step task on the small on-device brain",
                {"warning": warning}))
        except Exception:  # noqa: BLE001 — a broken gate denies
            return False

    async def _lite_plan_gate(self, emit) -> str | None:
        """The item-36 gate shared by run_plan and run_goal
        (v5.49): returns None when the run may proceed, else the
        honest not-run message (also emitted).

        Tier source of truth: _is_lite_tier_model() — i.e.
        core.voice.local_assets.effective_model(), the SAME resolution
        the doctor reports; no second sizing lives here. Non-lite
        tiers return None immediately with zero behaviour change.
        On the lite tier the FIRST plan/goal attempt per session
        emits LITE_PLAN_WARNING through `emit` (the run's own
        transcript channel) and requires an explicit yes via
        self.confirm_lite_plan (sync or async); a yes latches
        _lite_plan_confirmed so the session never re-warns, a no
        (or an unanswerable default) runs nothing."""
        if self._lite_plan_confirmed or not _is_lite_tier_model():
            return None
        emit(self.LITE_PLAN_WARNING)
        callback = getattr(self, "confirm_lite_plan", None)
        if not callable(callback):
            callback = self._default_confirm_lite_plan
        try:
            if asyncio.iscoroutinefunction(callback):
                approved = await callback(self.LITE_PLAN_WARNING)
            else:
                # A sync callback may block waiting for the human
                # (the default goes through confirm_gate) — never
                # block the session's event loop on it.
                loop = asyncio.get_running_loop()
                approved = await loop.run_in_executor(
                    None, callback, self.LITE_PLAN_WARNING)
                if asyncio.iscoroutine(approved) or hasattr(approved, "__await__"):
                    approved = await approved
        except Exception:  # noqa: BLE001 — an unanswered ask is a no
            approved = False
        if approved:
            self._lite_plan_confirmed = True
            return None
        message = (
            f"{self.LITE_PLAN_WARNING} It was not confirmed, so "
            "this multi-step task was not run.")
        emit(message)
        return message
