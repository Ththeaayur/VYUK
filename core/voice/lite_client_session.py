# INTENTIONAL (B27): LiteLiveSession moved whole verbatim from core/voice/lite_client.py (old lines 218-908); class kept whole per §3.9 (is_connected property + setter in original order).
import asyncio
import queue
import time
from collections.abc import Callable
from typing import Any

from .. import failsafe, injection  # Phase 3 V1b: moved to core/voice/
from .smartturn import (  # GAPS-25 (V-2): turn-detector consultation
    consult_turn_detector as _consult_turn_detector)
from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1b interim closed
from ..taskwake import cancel_waking  # Phase 3 V1b: moved to core/voice/
from ..config import config  # Phase 3 V1b: moved to core/voice/
from ..provider_identity import Engine
from ..memory.memory import memory_store  # Phase 3 V1b: moved to core/voice/
from ..funnel.tools_registry import tools  # Phase 3 V1b: moved to core/voice/; Phase 4 S1b INTENTIONAL: registry split to funnel

# INTENTIONAL (B27): cross-part names owned by lite_client_helpers (bare-name rule §2 P3).
from .lite_client_helpers import (
    _bump_epoch,
    WATCH_INTERVAL_S,
    _IMAGE_REPLY_COOLDOWN_S,
    _READY_LINE,
    _UPGRADE_FAILED_LINE,
    _IMAGE_LINE,
    _route_intent,
)


class LiteLiveSession:
    """First-run session with the full session surface (see module docstring)."""

    #: GAPS-22 (B-V3/B-V4): this session's engine identity — the
    #: local engine, in lite form (``is_lite`` replaces the old
    #: ``type(inner).__name__ == "LiteLiveSession"`` checks).
    #: ``connected_model`` stays None: the lite session serves no
    #: GGUF — the full model is the upgrade target, not this
    #: session's model.
    engine = Engine.LOCAL
    is_lite = True
    connected_model = None

    def __init__(self):
        self._connected = False
        self._stopped = False
        self._audio_manager = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

        # Callbacks — identical surface to GeminiLiveSession
        self.on_status_change: Callable[[str], None] | None = None
        self.on_transcript: Callable[[str, str], None] | None = None
        self.on_audio_data: Callable[[bytes], None] | None = None
        self.on_tool_call: Callable[[str, dict[str, Any], Any], None] | None = None

        self._turn_lock = asyncio.Lock()
        self._speaking = False
        self._whisper = None               # faster-whisper model, lazily loaded
        self._speech_helper = None         # LocalLiveSession used ONLY as a TTS engine
        self._mic_task: asyncio.Task | None = None
        self._mic_started = False
        self._watcher_task: asyncio.Task | None = None

        self._inner = None                 # the real LocalLiveSession after upgrade
        self._inner_task: asyncio.Task | None = None
        self._upgraded = False
        self._upgrade_attempted = False    # upgrade happens at most once
        self._upgrade_failed_noted = False
        self._last_image_reply = 0.0
        self._brain_downloaded_since: float | None = None  # v3.2: when the
        # "downloaded, now loading" state was first reported (the >60 s
        # stuck hint in _getting_ready_line keys off it)

    def release_model(self) -> None:
        """Drop the lazy Whisper instance (GAPS-22, B-V6).

        Only ever called on a never-started standby session, so
        ``_inner`` is None and untouched. The Whisper model is
        the shared STT factory's instance — released through the
        factory, mirroring the full session's idle unload — so
        the release actually frees the RAM. Never raises.
        """
        if self._whisper is None:
            return
        try:
            from .asr import release_whisper_model as _release

            _release(self._whisper)
        except Exception:  # noqa: BLE001 — the release never breaks the discard
            pass
        self._whisper = None

    @property
    def client(self) -> None:
        """No genai client exists on the local engine (brain.client contract)."""
        return None

    @property
    def is_connected(self) -> bool:
        if self._upgraded and self._inner is not None:
            if self._inner.is_connected:
                return True
            # The inner session is still loading its model — the
            # session is alive, just not listening yet.
            return self._inner_task is not None and not self._inner_task.done()
        return self._connected

    @is_connected.setter
    def is_connected(self, value: bool):
        self._connected = bool(value)

    # ------------------------------------------------------------------ #
    # Small plumbing
    # ------------------------------------------------------------------ #

    def _notify_status(self, status: str):
        if self.on_status_change:
            self.on_status_change(status)

    def _emit_assistant(self, text: str):
        """Emits an assistant transcript + history log — the ONLY way
        lite speech reaches the transcript (landmine 15: never as a
        user line, so learning never sees lite's own words)."""
        if self.on_transcript:
            self.on_transcript("assistant", text)
        memory_store.log_message("assistant", text)

    def _emit_user(self, text: str):
        if self.on_transcript:
            self.on_transcript("user", text)
        memory_store.log_message("user", text)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self, audio_manager):
        """Runs the lite session until stop(): kicks the pack download,
        starts listening (typed input immediately; the mic as soon as
        the ears land), and watches for the full brain's arrival."""
        self._audio_manager = audio_manager
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._stopped = False
        self._notify_status("CONNECTING")
        _bump_epoch("lite start")  # v5.48 (programme item 20)
        self._kick_downloads()
        self._connected = True
        self._notify_status("LISTENING")
        if local_assets.whisper_ready():
            self._start_mic()
        self._watcher_task = asyncio.create_task(self._watch_loop())
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            self._teardown()
            self._connected = False
            _bump_epoch("lite session end")  # v5.48 (programme item 20)
            if not self._upgraded:
                # After an upgrade the inner session owns the
                # DISCONNECTED notification (its callbacks are the
                # window's, wired at handoff).
                self._notify_status("DISCONNECTED")

    def set_speech_speed_override(self, factor):
        """v5.17 (Mouth M18): lite speaks through its helper (and,
        after the upgrade, the real session) — forward the pace
        override to whichever voice is live."""
        for target in (self._inner, self._speech_helper):
            setter = getattr(target, "set_speech_speed_override", None) \
                if target is not None else None
            if callable(setter):
                try:
                    setter(factor)
                except Exception:  # noqa: BLE001
                    pass

    def stop(self):
        self._stopped = True
        self._connected = False
        _bump_epoch("lite stop")  # v5.48 (programme item 20)
        if self._inner is not None:
            try:
                self._inner.stop()
            except Exception:  # noqa: BLE001 — stop must never raise
                pass
        if self._speech_helper is not None:
            self._speech_helper._running = False
            self._speech_helper._speaking = False
        for task in (self._watcher_task, self._mic_task):
            # v5.32: wake the worker loop to deliver the cancel,
            # like the stop event below already does
            # (core/taskwake.py).
            cancel_waking(task)
        if self._loop is not None and self._stop_event is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                pass  # loop already closed — the session is over anyway

    def _teardown(self):
        for task in (self._watcher_task, self._mic_task):
            if task is not None and not task.done():
                task.cancel()
        if self._speech_helper is not None:
            self._speech_helper._running = False
        if self._upgraded and self._inner is not None:
            # start() was cancelled from outside after the upgrade —
            # don't leave the real session running orphaned.
            try:
                self._inner.stop()
            except Exception:  # noqa: BLE001 — teardown must never raise
                pass

    def _kick_downloads(self):
        """Starts the offline-pack download in the background via the
        DownloadManager singleton (which refuses a double start), so
        the pack keeps downloading across the upgrade and even if the
        user disconnects and reconnects into the real session."""
        try:
            if (local_assets.brain_ready() and local_assets.whisper_ready()
                    and local_assets.voice_ready()):
                return
            if local_assets.downloads.is_running():
                return
            local_assets.downloads.start(self._forward_setup_line)
        except Exception as e:  # noqa: BLE001 — downloads are best-effort here
            print(f"[LiteSession] Could not start background downloads: {e}")

    def _forward_setup_line(self, line: str):
        # Download progress surfaces as SETUP: statuses, exactly like
        # the real session's _prepare_builtin forwarding.
        if line and not line.startswith("ERROR:"):
            self._notify_status("SETUP: " + line)

    # ------------------------------------------------------------------ #
    # Readiness watcher + auto-upgrade
    # ------------------------------------------------------------------ #

    async def _watch_loop(self):
        """Every WATCH_INTERVAL_S: start the mic when the ears land;
        upgrade to the real session when ears + brain are both ready."""
        try:
            while not self._stopped and not self._upgraded:
                await asyncio.sleep(WATCH_INTERVAL_S)
                if self._stopped or self._upgraded:
                    break
                if not self._mic_started and local_assets.whisper_ready():
                    self._start_mic()
                if (not self._upgrade_attempted
                        and local_assets.brain_ready()
                        and local_assets.whisper_ready()):
                    await self._upgrade()
                    break
        except asyncio.CancelledError:
            pass

    async def _upgrade(self):
        """Hands the session to a real LocalLiveSession, at most once.

        The announcement is emitted through lite's own transcript
        BEFORE the handoff, with lite's callbacks; from then on the
        inner session holds the window's callbacks directly and lite
        only delegates. If the inner start fails (its task ends
        without connecting), lite resumes — and says so, once.
        """
        self._upgrade_attempted = True
        # v5.48 (programme item 20): the handoff swaps which
        # session is live — approvals in flight from the lite
        # session are void from here.
        _bump_epoch("lite upgrade — session swap")
        from .local_client import LocalLiveSession

        inner = LocalLiveSession()
        inner.on_status_change = self.on_status_change
        inner.on_transcript = self.on_transcript
        inner.on_audio_data = self.on_audio_data
        inner.on_tool_call = self.on_tool_call

        self._emit_assistant(_READY_LINE)
        self._stop_mic()
        self._inner = inner
        self._upgraded = True
        self._inner_task = asyncio.create_task(inner.start(self._audio_manager))
        self._inner_task.add_done_callback(self._on_inner_done)

    def _on_inner_done(self, task: asyncio.Task):
        if self._stopped or not self._upgraded:
            return
        if not task.cancelled() and task.exception() is not None:
            print(f"[LiteSession] Upgraded session ended with: {task.exception()}")
        inner = self._inner
        if inner is not None and not inner.is_connected:
            # The real start returned without a session (asset/model
            # failure — it already emitted its ERROR status). Fall
            # back to lite and say so, once.
            self._inner = None
            self._inner_task = None
            self._upgraded = False
            if local_assets.whisper_ready():
                self._start_mic()
            if not self._upgrade_failed_noted:
                self._upgrade_failed_noted = True
                self._emit_assistant(_UPGRADE_FAILED_LINE)

    # ------------------------------------------------------------------ #
    # Mic loop + ears (GAPS-16: the loop itself is the one shared
    # loop in core/voice/mic_loop.py — this session only supplies
    # its hooks; transcription goes through the shared STT factory
    # in core/voice/asr.py, on a model shared with the full
    # session — the lite session no longer constructs its own)
    # ------------------------------------------------------------------ #

    def _start_mic(self):
        if self._mic_started or self._audio_manager is None:
            return
        self._mic_started = True
        self._mic_task = asyncio.create_task(self._mic_loop(self._audio_manager))

    def _stop_mic(self):
        if self._mic_task is not None and not self._mic_task.done():
            self._mic_task.cancel()
        self._mic_task = None
        self._mic_started = False

    async def _mic_loop(self, audio_manager):
        """The lite session's mic loop — now a thin wrapper over the
        one shared loop (core/voice/mic_loop.run_mic_loop). The
        state machine lives there; this method only supplies the
        lite session's hooks: its own NoiseFloorEstimator (same
        class, same barge-in threshold, sustain window and
        post-playback guard as the full session), its faded
        interrupt through the speech helper (or the audio
        manager's hard cut when there is no helper), and its
        task-per-utterance dispatch — a lite turn is tasked, not
        awaited, so the loop keeps draining while it runs;
        turns stay sequential on the _turn_lock. No pre-roll and no
        listen timeout: the lite session never had either, and
        this wrapper does not add them."""
        from .local_client_vad import NoiseFloorEstimator
        from .mic_loop import run_mic_loop

        def _interrupt():
            # v5.1: route through the helper's faded interrupt (it
            # shares this audio manager) so lite barge-ins fade
            # instead of clipping; hard cut only if there's no
            # helper.
            if self._speech_helper is not None:
                self._speech_helper._interrupt_playback()
                self._speech_helper._speaking = False
            else:
                audio_manager.interrupt_playback()

        async def _dispatch_utterance(pcm_bytes, **kwargs):
            # Voice-control batch (2026-11-16, V3): schedule the
            # turn and return at once — the old inline dispatch
            # awaited the whole turn in the loop, so speech
            # during a reply was queue-dropped at the callback
            # before VAD ever saw it. Turns stay sequential
            # (_process_utterance_turn holds the _turn_lock), but
            # the loop keeps draining while a turn runs, exactly
            # like the local/Groq sessions. A failed turn is
            # logged, never allowed to kill the loop.
            async def _guarded():
                try:
                    await self._process_utterance(pcm_bytes, **kwargs)
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001
                    print(f"[LiteSession] Turn failed: {e}")
            asyncio.create_task(_guarded())

        await run_mic_loop(
            audio_manager,
            running=lambda: not self._stopped and not self._upgraded,
            is_speaking=lambda: self._speaking,
            set_speaking=lambda value: setattr(self, "_speaking", value),
            interrupt_playback=_interrupt,
            on_utterance=_dispatch_utterance,
            estimator=NoiseFloorEstimator(),
        )

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
            # v5.19: the full session's Whisper-hallucination guard,
            # imported like the VAD gate below — a stock phrase heard
            # in near-silence is the recogniser dreaming, not the user.
            from .local_client import (
                LocalLiveSession,
                _is_probable_hallucination,
            )
            if _is_probable_hallucination(
                    text, LocalLiveSession._max_chunk_rms(pcm_bytes)):
                print("[LiteSession] Dropped probable Whisper "
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
            self._emit_user(text)
            await self._respond(text)

    def _ensure_whisper(self) -> bool:
        """Loads (and caches) faster-whisper — GAPS-16: from the one
        shared STT factory (core/voice/asr.get_whisper_model), the
        same construction this method used to perform itself (the
        effective model argument from local_assets.
        whisper_model_arg() — v5.45 A10 wiring — device auto,
        compute_type int8). The model is shared with the full
        session (and the meeting path) instead of loaded once per
        session; the lite session holds its own reference, so an
        idle unload of the full session's reference never drops
        the model under it. The load exception itself is printed
        by the factory."""
        if self._whisper is not None:
            return True
        try:
            from .asr import (
                faster_whisper_available as _fw_available,
                get_whisper_model as _get_whisper_model,
            )
        except Exception:  # noqa: BLE001
            print("[LiteSession] faster-whisper is not installed — "
                  "typed input only until the ears can load.")
            return False
        if not _fw_available():
            print("[LiteSession] faster-whisper is not installed — "
                  "typed input only until the ears can load.")
            return False
        self._whisper = _get_whisper_model()
        if self._whisper is None:
            print("[LiteSession] Whisper model failed to load.")
            return False
        return True

    def _transcribe(self, pcm_bytes: bytes) -> str:
        """Sync transcription (executor only), mirroring
        LocalLiveSession._transcribe; "" when the ears can't load.

        v5.44 (A1b): the transcribe() kwargs come from the SAME
        shared resolver the main path uses —
        core.local_client._transcription_settings, imported here,
        never forked or copied — so the lite brain's Whisper call
        gets the locked language, beam, thresholds and biasing
        prompt exactly as the full session's does (it previously
        called transcribe() bare, with a greedy beam of 1).
        Voice-control batch (2026-11-16, V5): the call itself is
        now the shared asr.transcribe_text too — one STT call
        for live turns and meetings alike.
        """
        if not self._ensure_whisper():
            return ""
        try:
            from .asr import transcribe_text as _transcribe_text
            from .local_client import _transcription_settings

            return _transcribe_text(
                pcm_bytes, word_timestamps=False,
                **_transcription_settings())
        except Exception as e:  # noqa: BLE001
            print(f"[LiteSession] Transcription failed: {e}")
            return ""

    # ------------------------------------------------------------------ #
    # Turns: intent responder → speech
    # ------------------------------------------------------------------ #

    async def send_text(self, text: str):
        """Typed input. After the upgrade, delegates to the real session.

        Phase 1 (F04): returns a delivery result instead of a
        silent no-op — {"status": "dropped", "reason": ...} when
        no session is live, {"status": "ignored", "reason":
        "empty"} for a blank turn, {"status": "accepted"} once
        the turn ran (or was delegated after upgrade)."""
        if self._upgraded and self._inner is not None:
            result = await self._inner.send_text(text)
            if isinstance(result, dict) and result.get("status"):
                return result
            return {"status": "accepted"}
        if not self.is_connected:
            return {"status": "dropped", "reason": "not connected"}
        text = (text or "").strip()
        if not text:
            return {"status": "ignored", "reason": "empty"}
        self._emit_user(text)
        async with self._turn_lock:
            await self._respond(text)
        return {"status": "accepted"}

    async def _respond(self, text: str):
        """One lite turn: route → execute (executor; tools can block)
        → assistant transcript → speech. Caller holds the turn lock
        and has already emitted the user transcript."""
        # A genuine user turn (typed or spoken) lifts any injection
        # taint (v5.3) — same rule as the full sessions; bracketed
        # internal texts are not the user taking the wheel.
        if not (text or "").lstrip().startswith("["):
            injection.clear_taint()
            # v5.52 (item 7): publish the turn's instruction for
            # the executor's echo guard (core/brain/turn_context).
            try:
                from ..brain import turn_context as _tc  # Phase 3 V1b: moved to core/voice/

                _tc.set_current_instruction(text)
            except Exception:  # noqa: BLE001 — never break a turn
                pass
        # v5.42 (H14): a BARE cancel-all utterance ("stop", "vyuk
        # stop", "cancel everything", "enough" — core/failsafe's
        # tight predicate) is handled pre-intent, exactly like the
        # full local session's pre-model check: cut any in-flight
        # playback (the speech helper's faded interrupt when there
        # is one, else the audio manager's hard cut), confirm with
        # "Stopped.", and never route to an intent. Both lite entry
        # points (mic _process_utterance, typed send_text) funnel
        # through _respond, so this one check covers both.
        try:
            _is_cancel = failsafe.is_cancel_all_utterance(text)
        except Exception:  # noqa: BLE001
            _is_cancel = False
        if _is_cancel:
            try:
                if self._speech_helper is not None:
                    self._speech_helper._interrupt_playback()
                    self._speech_helper._speaking = False
                elif self._audio_manager is not None:
                    self._audio_manager.interrupt_playback()
            except Exception:  # noqa: BLE001 — the cut is best-effort
                pass
            self._speaking = False
            self._emit_assistant("Stopped.")
            try:
                await self._speak("Stopped.")
            except Exception:  # noqa: BLE001 — confirmation is degradable
                pass
            self._notify_status("LISTENING")
            return
        self._notify_status("THINKING")
        loop = asyncio.get_running_loop()
        reply, tool_call = await loop.run_in_executor(None, self._intent_reply, text)
        if tool_call is not None and self.on_tool_call:
            name, args, result = tool_call
            self.on_tool_call(name, args, result)
        self._emit_assistant(reply)
        # v5.51 (M-4): the chat line above keeps the full result
        # text, but an error result is SPOKEN as the butler line
        # (core/voice/spoken_errors) — the lite session speaks its
        # intent replies verbatim, so this is a real choke point.
        speak_text = reply
        if tool_call is not None:
            try:
                from .spoken_errors import spoken_error_text  # Phase 3 V1b: moved to core/voice/

                line = spoken_error_text(tool_call[2])
                if line:
                    speak_text = line
            except Exception:  # noqa: BLE001 — speech is degradable
                pass
        await self._speak(speak_text)

    def _getting_ready_line(self) -> str:
        """The honest fallback for anything the lite session can't do,
        built from local_assets.pack_progress() (v3.2): the CURRENT
        stage named in plain words + real byte counts, never a
        percentage scraped from a status line and never 100% — or a
        stage's numbers — that isn't actually known."""
        abilities = (
            "I can already check the time, your system, weather, your day "
            "and your mail, and open apps."
        )
        try:
            progress = local_assets.pack_progress()
        except Exception:  # noqa: BLE001 — the line must never crash a turn
            return f"I'm still getting myself ready. {abilities}"
        by_stage = {s.get("stage"): s for s in progress.get("stages", [])}
        brain = by_stage.get("brain") or {}

        # Downloaded-but-not-loaded: the brain's bytes are all on
        # disk (or the brain already reports ready) yet this session
        # is still the one answering — the real session is starting.
        brain_bytes_done = (
            brain.get("total_bytes")
            and int(brain.get("done_bytes") or 0) >= int(brain["total_bytes"])
        )
        if progress.get("ready", {}).get("brain") or brain_bytes_done:
            now = time.monotonic()
            if self._brain_downloaded_since is None:
                self._brain_downloaded_since = now
            line = ("My brain has downloaded — I'm loading it now, "
                    "a few more seconds.")
            if now - self._brain_downloaded_since > 60:
                line += " If this sticks, see Settings → Advanced."
            return f"{line} {abilities}"
        self._brain_downloaded_since = None

        if progress.get("state") == "paused":
            return ("I'm still setting up — my setup download is paused; "
                    f"you can resume it from the strip at the top of the "
                    f"window or in Settings. {abilities}")

        stage_names = {
            "ears": "my ears are downloading",
            "voice": "my voice is downloading",
            "brain": "my brain is downloading",
        }
        stage = progress.get("current_stage") or "brain"
        clause = stage_names.get(stage, "my brain is downloading")
        info = by_stage.get(stage) or {}
        total = info.get("total_bytes")
        if total:
            clause += (f", {local_assets.format_bytes(int(info.get('done_bytes') or 0))}"
                       f" of {local_assets.format_bytes(int(total))} so far")
        else:
            overall = progress.get("overall_pct")
            if overall is not None:
                clause += f" (about {max(0, min(99, int(overall)))}% of setup overall)"
        return f"I'm still setting up — {clause}. {abilities}"

    def _intent_reply(self, text: str) -> tuple[str, tuple[str, dict[str, Any], Any] | None]:
        """Sync intent execution (executor only). Returns the reply
        text plus the (name, args, result) of the tool that produced
        it, when one ran."""
        from .local_client import _result_to_text

        routed = _route_intent(text)
        if routed is None:
            return (self._getting_ready_line(), None)
        name, args = routed
        result = tools.execute(name, args)
        reply = _result_to_text(result)
        # B12 (v5.45): a lite intent execution completes matching
        # plan steps too — same observer as every other client.
        try:
            from ..brain import planner as _planner  # Phase 3 V1b: moved to core/voice/

            _anns = _planner.observe_tool_execution(name, result, args)
            if _anns:
                reply += " " + " ".join(_anns)
        except Exception:  # noqa: BLE001 — bookkeeping never breaks a turn
            pass
        return (reply, (name, args, result))

    # ------------------------------------------------------------------ #
    # Speech out — a LocalLiveSession instance used purely as a TTS
    # engine, so the voice chain (Kokoro → Piper → SAPI → text-only),
    # the on_audio_data + enqueue_playback contract, and the SPEAKING
    # status flow are the real session's, not a re-implementation.
    # ------------------------------------------------------------------ #

    def _ensure_speech_helper(self):
        if self._speech_helper is None:
            from .local_client import LocalLiveSession

            helper = LocalLiveSession()
            helper._backend = "builtin"
            helper.on_status_change = self._notify_status
            helper.on_audio_data = self._forward_audio
            self._speech_helper = helper
        self._speech_helper._audio_manager = self._audio_manager
        self._speech_helper._running = not self._stopped
        return self._speech_helper

    def _forward_audio(self, pcm: bytes):
        if self.on_audio_data:
            self.on_audio_data(pcm)

    async def _speak(self, text: str):
        if not text:
            return
        try:
            helper = self._ensure_speech_helper()
        except Exception as e:  # noqa: BLE001 — voice is degradable, text already landed
            print(f"[LiteSession] Speech helper unavailable: {e}")
            self._notify_status("LISTENING")
            return
        self._speaking = True
        try:
            await helper._speak(text)
        except Exception as e:  # noqa: BLE001
            print(f"[LiteSession] Speech failed: {e}")
            self._notify_status("LISTENING")
        finally:
            self._speaking = False

    # ------------------------------------------------------------------ #
    # Vision (GUI entry point)
    # ------------------------------------------------------------------ #

    async def send_image_frame(self, image_bytes: bytes, mime_type: str = "image/jpeg"):
        """Lite can't read frames yet: say so — once per share (the
        cooldown keeps a frame stream from repeating the line) — and
        point at the OCR help the full session offers. After the
        upgrade, delegates to the real session."""
        if self._upgraded and self._inner is not None:
            await self._inner.send_image_frame(image_bytes, mime_type)
            return
        if not self.is_connected:
            return
        if time.monotonic() - self._last_image_reply < _IMAGE_REPLY_COOLDOWN_S:
            return
        self._last_image_reply = time.monotonic()
        async with self._turn_lock:
            self._emit_assistant(_IMAGE_LINE)
            await self._speak(_IMAGE_LINE)
