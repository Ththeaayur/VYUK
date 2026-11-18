"""GroqSession A mixin (B39 part 2 of 2) — moved verbatim
from core/voice/cloud_text_client.py; the composed class lives
in cloud_text_client.py. No behaviour change."""

from __future__ import annotations

import asyncio
import queue

from ..groq_action_journal import GroqActionJournal  # Phase 3 V1b: moved to core/voice/
from ..taskwake import cancel_waking  # Phase 3 V1b: moved to core/voice/
from ..config import DATA_DIR, config  # Phase 3 V1b: moved to core/voice/
from ..memory import episodes  # Phase 3 V1b: moved to core/voice/
from .. import injection  # Phase 3 V1b: moved to core/voice/
from .smartturn import (  # GAPS-25 (V-2): turn-detector consultation
    consult_turn_detector as _consult_turn_detector)
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..languages import reply_language_directive  # Phase 3 V1b: moved to core/voice/

# INTENTIONAL (B39 part 2 of 2): methods moved verbatim
# from cloud_text_client.py resolve helper names
# through cloud_text_helpers (part 1's module).
from .cloud_text_helpers import (
    HISTORY_CAP,
    NO_KEY_STATUS,
    _ambient_packet_text,
    _bump_epoch,
    _groq_api_key,
    _groq_model,
    _groq_tool_router_enabled,
    _groq_vision_enabled,
    _log_line,
    groq_offered_declarations,
)


class GroqSessionLifecycleMixin:

    def __init__(self):
        self.is_connected = False
        self._running = False
        self._session_tasks: list[asyncio.Task] = []
        self._turn_tasks: set[asyncio.Task] = set()
        self._audio_manager = None
        # v5.49 (item 36): parity with LocalLiveSession — never
        # consulted on a cloud brain (the lite gate cannot fire);
        # present so the session surface is uniform.
        self.confirm_lite_plan: Callable[[str], Any] = (
            lambda warning: True)

        # Callbacks — identical surface to GeminiLiveSession
        self.on_status_change: Callable[[str], None] | None = None
        self.on_transcript: Callable[[str, str], None] | None = None
        self.on_audio_data: Callable[[bytes], None] | None = None
        self.on_tool_call: Callable[[str, dict[str, Any], Any], None] | None = None

        self._history: list[dict[str, Any]] = []
        # Phase 0 (C02): durable per-action journal — the separate
        # truth that survives the turn-failure history rollback.
        # Constructed eagerly (no file IO until the first event).
        self._action_journal = GroqActionJournal()
        # Current turn's journal identity, set by _brain_turn.
        self._groq_turn_id = ""
        self._groq_turn_label = ""
        self._turn_lock = asyncio.Lock()
        self._ambient_packet: str = ""       # v5.44 (N4)
        self._ambient_context_pending: str = ""  # …for the next turn
        self._system_prompt = ""        # built once, at start()
        self._speaking = False
        self._speech_helper = None      # LocalLiveSession as ears+voice

    @property
    def client(self) -> None:
        """No genai client exists on this engine (brain.client contract)."""
        return None

    @property
    def native_frame_vision(self) -> bool:
        """E5a: this session consumes frames as REAL vision (the
        window's frame-sink delivery reads this — gui/main_window's
        _deliver_tool_frame) whenever vision is enabled and a key
        exists. When it reads False the sink keeps the OCR-text
        delivery, and send_image_frame's own degrade agrees."""
        return _groq_vision_enabled() and bool(_groq_api_key())

    @property
    def voice_ready(self) -> bool:
        """Post-ship v5.98 follow-up (battery v515_groq surface
        parity): mirrors local_client LocalLiveSession.voice_ready
        (property, same name, same semantics — voice actually
        available IN THIS SESSION OBJECT). A Groq text session
        carries no STT ears itself — the mic loop is a separate
        component, not part of this object — so False is the
        truthful value, not a placeholder. Safe on every dynamic
        reader: brain.py readiness() handles False explicitly
        (voice unavailable, typed input still works), and its
        groq-provider branch never consults this property at all
        (it reports the provider-level mic loop instead)."""
        return False

    def _notify_status(self, status: str):
        if self.on_status_change:
            self.on_status_change(status)

    def _build_system_prompt(self) -> str:
        """Assembled the cloud way (live_client.start): the persona,
        the memory context read ONCE at session start, then the
        injection rule verbatim."""
        prompt = config.system_instruction
        # v5.41: non-English home directive ("" for English).
        prompt += reply_language_directive()
        memories = memory_store.get_memory_context_prompt()
        # Phase 2 (audit 6 B03): the start-time memory block,
        # against which each turn's refresh is compared — a
        # refresh that matches it byte-for-byte is skipped, so
        # the fast path leaves the system prompt unchanged.
        self._start_memory_block = memories
        if memories:
            prompt += f"\n\n{memories}"
        # Conversion Wave 8: PF-28 learning-feed fact lines +
        # sleeptime ranked file-store block ("" on a default
        # install, so the prompt stays byte-identical; see
        # live_client._build_live_prompt for the same seam).
        try:
            from .. import w8_wiring as _w8  # Phase 3 V1b: moved to core/voice/

            _supplements = _w8.prompt_supplements()
        except Exception:  # noqa: BLE001 — supplements never break
            _supplements = ""
        if _supplements:
            prompt += _supplements
        # Ambient context (v5.44, N4): the NOSE packet rides at
        # the same place the memory block goes; "" adds nothing,
        # so an empty packet leaves the prompt byte-identical.
        ambient_packet = _ambient_packet_text()
        self._ambient_packet = ambient_packet
        if ambient_packet:
            prompt += f"\n\n{ambient_packet}"
        # Continuity (v5.43, B14): the episodes day-render (today
        # + yesterday, small-capped — core/episodes, the one
        # continuity design) folds in at session start alongside
        # the memory block; the conversation itself is seeded into
        # history in start() (B10). "" when the journal is empty —
        # the prompt is then byte-identical to before.
        try:
            day_block = episodes.day_render_block()
        except Exception:  # noqa: BLE001 — continuity never blocks
            day_block = ""
        if day_block:
            prompt += f"\n\n{day_block}"
        prompt += f"\n\n{injection.PROMPT_RULE}"
        # B8 (v5.45): a routed Groq session teaches the model the
        # router protocol, exactly like a routed Live session.
        if _groq_tool_router_enabled():
            try:
                from ..funnel.toolbox import ROUTER_PROMPT  # Phase 3 V1b: moved to core/voice/; Phase 3 F2a: toolbox moved to core/funnel/

                prompt += f"\n\n{ROUTER_PROMPT}"
            except Exception:  # noqa: BLE001
                pass
        # Conversion Wave 8: context ledger + prompt-cache-first
        # assembly, enforced BEFORE the brain call (same seam as
        # live_client._build_live_prompt: stable-prefix SHA-256
        # key recorded on the session, full prompt pre-flighted,
        # supplements dropped first, then fail-closed refuse).
        try:
            from .. import w8_wiring as _w8  # Phase 3 V1b: moved to core/voice/
        except Exception:  # noqa: BLE001
            _w8 = None
        if _w8 is not None:
            try:
                _cached = _w8.note_stable_prefix(
                    [config.system_instruction,
                     reply_language_directive()])
                self._w8_cache_key = _cached.cache_key
            except Exception:  # noqa: BLE001 — cache key is advisory
                pass
            prompt, _preflight = _w8.enforce_prompt(
                prompt, memories, _supplements)
        return prompt

    async def start(self, audio_manager):
        """Runs the session until stop(). A missing key refuses
        here — the ERROR line, a log trace, zero HTTP — mirroring
        live_client's MISSING_API_KEY early return."""
        injection.clear_taint()  # fresh session, fresh trust state (v5.3)
        _bump_epoch("groq start")  # v5.48 (programme item 20)
        self._notify_status("CONNECTING")
        if not _groq_api_key():
            _log_line("Start refused: no Groq API key configured.")
            self._notify_status(NO_KEY_STATUS)
            return

        # S13: diagnosability parity with the Gemini path,
        # whose pre-flight prints its payload size — a Groq
        # session states what it is about to offer, so a
        # tool-count problem is visible in the log, not a
        # mystery at the first refused turn.
        try:
            _offered = len(groq_offered_declarations())
        except Exception:  # noqa: BLE001
            _offered = -1
        _log_line(f"Offering {_offered} tool declarations "
                  f"this session (router "
                  f"{'on' if _groq_tool_router_enabled() else 'off'}).")
        self._system_prompt = self._build_system_prompt()
        # Continuity (v5.43, B10): seed the message history from
        # the stored conversation logs — the last ~6 exchanges —
        # so a Groq session resumes the conversation instead of
        # starting cold. In-memory only (nothing is re-logged),
        # and only into an empty history.
        if not self._history:
            try:
                seeded = episodes.seed_history_messages()
            except Exception:  # noqa: BLE001
                seeded = []
            if seeded:
                self._history = seeded[-HISTORY_CAP:]
        self._audio_manager = audio_manager
        self._running = True
        self.is_connected = True
        # GAPS-22 (B-V5): the model actually serving — the Groq
        # model the start resolved (env GROQ_MODEL or the default).
        self.connected_model = _groq_model()
        self._notify_status("LISTENING")

        mic_task = asyncio.create_task(self._mic_loop(audio_manager))
        self._session_tasks = [mic_task]
        try:
            await asyncio.gather(*self._session_tasks)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            _log_line(f"Session error: {e}")
            self._notify_status(f"ERROR: {str(e)[:300]}")
        finally:
            self.is_connected = False
            self._running = False
            _bump_epoch("groq session end")  # v5.48 (programme item 20)
            self._notify_status("DISCONNECTED")

    def set_ambient_context(self, packet: str):
        """BrainSession.refresh_ambient_context's Groq seam (N4):
        stash a changed packet; the next _brain_turn folds it
        into that turn's system message — nothing emitted or
        spoken here."""
        packet = packet or ""
        if packet:
            self._ambient_packet = packet
            self._ambient_context_pending = packet

    def refresh_ambient_context(self, packet: str | None = None):
        """N4 refresh (v5.44): the session-surface name the other
        clients expose (GeminiLiveSession's). Groq is a text
        brain, so a refresh is exactly the set_ambient_context
        stash — the changed packet folds into the next turn's
        system message; nothing is emitted or spoken here. With
        no packet given, re-pull the ambient packet (the same
        lazy seam session start uses). Unchanged/empty packets
        are a no-op, mirroring the Live client's contract."""
        if packet is None:
            packet = _ambient_packet_text()
        if not packet or packet == self._ambient_packet:
            return
        self.set_ambient_context(packet)

    def preload_model_only(self) -> bool:
        """B2 surface parity (v5.44): the warm-standby preload
        only ever targets the LOCAL brain's model — a cloud
        session has nothing on disk to preload. Honest no-op:
        returns False, like the local client's Ollama branch."""
        return False

    def set_speech_speed_override(self, factor):
        """v5.17 (Mouth M18): Groq speaks through its local speech
        helper, so the pace override forwards to it — the same
        contract as LiteLiveSession's."""
        helper = getattr(self, "_speech_helper", None)
        setter = getattr(helper, "set_speech_speed_override", None) \
            if helper is not None else None
        if callable(setter):
            try:
                setter(factor)
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        self._running = False
        _bump_epoch("groq stop")  # v5.48 (programme item 20)
        if self._speech_helper is not None:
            self._speech_helper._running = False
            self._speech_helper._speaking = False
        # v5.32: wake the worker loop to deliver these cancels
        # (core/taskwake.py).
        for task in self._session_tasks:
            cancel_waking(task)
        for task in list(self._turn_tasks):
            cancel_waking(task)

    def _ensure_speech_helper(self):
        if self._speech_helper is None:
            from .local_client import LocalLiveSession

            helper = LocalLiveSession()
            helper._backend = "builtin"
            helper.on_status_change = self._notify_status
            helper.on_audio_data = self._forward_audio
            self._speech_helper = helper
        self._speech_helper._audio_manager = self._audio_manager
        self._speech_helper._running = self._running
        return self._speech_helper

    def _forward_audio(self, pcm: bytes):
        if self.on_audio_data:
            self.on_audio_data(pcm)

    def _transcribe(self, pcm_bytes: bytes) -> str:
        """Sync transcription (executor only) — the helper's real
        faster-whisper path, bundled-model aware, identical to the
        local session's ears. "" when the ears can't load."""
        helper = self._ensure_speech_helper()
        return helper._transcribe(pcm_bytes)

    async def _speak(self, text: str):
        if not text:
            return
        try:
            helper = self._ensure_speech_helper()
        except Exception as e:  # noqa: BLE001 — voice is degradable, text already landed
            print(f"[GroqSession] Speech helper unavailable: {e}")
            self._notify_status("LISTENING")
            return
        self._speaking = True
        try:
            await helper._speak(text)
        except Exception as e:  # noqa: BLE001
            print(f"[GroqSession] Speech failed: {e}")
            self._notify_status("LISTENING")
        finally:
            self._speaking = False

    async def _mic_loop(self, audio_manager):
        """The Groq session's mic loop — now a thin wrapper over the
        one shared loop (core/voice/mic_loop.run_mic_loop). The
        state machine lives there; this method only supplies the
        Groq session's hooks: a fresh NoiseFloorEstimator, its
        faded interrupt through the speech helper (or the audio
        manager's hard cut when there is no helper), and its
        task-per-utterance dispatch.

        GAPS-16 behaviour changes (the flaw's fix, stated with
        numbers — see core/voice/mic_loop.py): this loop used to
        cut playback on ANY speech chunk while speaking (any chunk
        with RMS >= the plain VAD threshold, default 400) and
        scored speech against the plain config floor with no
        adaptive estimator. It now barges in like local/lite — a
        chunk must reach RMS >= VAD x 1.6 (default 640) AND
        sustain above that bar for 150 ms, with a 250 ms
        post-playback guard — and it scores against the adaptive
        floor (max(400, room-floor x 3.5), capped at 1600). In a
        quiet room the VAD threshold is identical (400); a single
        loud chunk no longer interrupts a Groq reply. No pre-roll
        and no listen timeout: the Groq session never had either,
        and this wrapper does not add them."""
        from .local_client_vad import NoiseFloorEstimator
        from .mic_loop import run_mic_loop

        def _interrupt():
            # Barge-in, mirroring the local + lite sessions:
            # route through the speech helper's FADED interrupt
            # (v5.1) so the cut doesn't clip; the manager's hard
            # cut only when there's no helper.
            if self._speech_helper is not None:
                self._speech_helper._interrupt_playback()
                self._speech_helper._speaking = False
            else:
                audio_manager.interrupt_playback()

        await run_mic_loop(
            audio_manager,
            running=lambda: self._running,
            is_speaking=lambda: self._speaking,
            set_speaking=lambda value: setattr(self, "_speaking", value),
            interrupt_playback=_interrupt,
            on_utterance=self._dispatch_utterance,
            estimator=NoiseFloorEstimator(),
        )

    async def _dispatch_utterance(self, pcm: bytes,
                                  trailing_silence_s: float = 0.0
                                  ) -> None:
        """One finished utterance: dispatch it as its own task (the
        shared loop awaits this, so the loop never blocks on a
        turn). GAPS-25 (V-2): the trailing silence that ended the
        utterance travels with it to _process_utterance, which
        hands it to the turn detector's consultation."""
        task = asyncio.create_task(
            self._process_utterance(pcm, trailing_silence_s))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

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
            # GAPS-25 (V-2): consult the turn detector on the
            # finished turn — audio, transcript and the trailing
            # silence that ended it. Observation only: the verdict
            # is recorded on the session and never gates dispatch;
            # the turn proceeds exactly as before.
            _consult_turn_detector(
                self, pcm_bytes, text, trailing_silence_s)
            # Genuine speech: the user is driving again, so any
            # injection taint lifts (v5.3 — same rule as local).
            injection.clear_taint()
            if self.on_transcript:
                self.on_transcript("user", text)
            memory_store.log_message("user", text)
            await self._brain_turn(text)
