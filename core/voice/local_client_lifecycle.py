# Batch 3 (plan §3.3): lifecycle-prep, moved verbatim
# from core/voice/local_client.py. INTENTIONAL: imports below are the
# subset of the old module's import block that this group's code uses.
import asyncio
import gc
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
import numpy as np
from ..config import config
from .. import injection
from ..taskwake import cancel_waking
from . import local_assets
from .local_client_guards import (
    HOUSEKEEPING_TICK_S, OLLAMA_CHAT_TIMEOUT_S, OLLAMA_TAGS_TIMEOUT_S,
    _ambient_packet_text, _bump_epoch)
from .local_client_cost import _cost_prompt_text, _cost_record_local
from .local_client_render import _gpu_load_suspect, _write_llama_load_state
from .local_client_budgets import _clear_active_tokenizer, _set_active_tokenizer
from .local_client_vad import NoiseFloorEstimator
from .local_client_vlm import _format_action_trace


class LocalLiveSessionLifecycleMixin:

    def __init__(self):
        self.is_connected = False
        self._running = False
        self._session_tasks: list[asyncio.Task] = []
        self._turn_tasks: set[asyncio.Task] = set()
        self._audio_manager = None

        # Callbacks — identical surface to GeminiLiveSession
        self.on_status_change: Callable[[str], None] | None = None
        self.on_transcript: Callable[[str, str], None] | None = None
        self.on_audio_data: Callable[[bytes], None] | None = None
        self.on_tool_call: Callable[[str, dict[str, Any], Any], None] | None = None

        self._history: list[dict[str, str]] = []
        # Turn-truthfulness (6th-B08): per-turn action trace —
        # compact structured records of what each turn's tools
        # actually did (tool, status, outcome snippet), kept
        # alongside _history (same in-memory lifetime) and
        # surfaced into the NEXT turn's system prompt so a later
        # turn can answer which concrete actions succeeded.
        # Separate from the visible response, which is only the
        # final spoken text.
        self._turn_action_trace: list[dict[str, Any]] = []
        self._ambient_packet: str = ""       # v5.44 (N4): packet held
        self._ambient_context_pending: str = ""  # …for the next turn
        self._continuity_day_block = ""  # v5.43 (B10/B14): the held
                                       # episodes day-render for the
                                       # first turn's assembly only
        self._day_block_pending = False  # one-shot flag for the above
        self._turn_lock = asyncio.Lock()
        self._last_brain_activity = time.monotonic()  # v3.2 idle unload
        self._last_stt_activity = time.monotonic()
        self._llama_unloaded = False   # set by the idle unload; the next
                                       # brain turn reloads and says so
        self._speaking = False
        self._vad_estimator = NoiseFloorEstimator()  # v5.44 (A3):
                                       # this session's rolling room-
                                       # noise floor; the mic loop
                                       # feeds it idle chunks only and
                                       # reads the effective threshold
                                       # back per chunk
        self._speech_speed_override: float | None = None  # v5.17 pace
                                       # override (set_speech_speed_
                                       # override); None = no override
        self._karaoke_pending: tuple[int, int, str] | None = None
                                       # (index, total, raw sentence)
                                       # the next _play_pcm enqueue
                                       # belongs to (v5.17 karaoke);
                                       # consumed inside _play_pcm
        self._last_utterance_at = time.monotonic()  # v5.17 listen
                                       # timeout clock: last completed
                                       # utterance dispatched by the
                                       # mic loop (session start
                                       # counts as the stretch start)
        self._speech_queue: asyncio.Queue | None = None  # live only inside _speak_chunked
        self._join_tail: np.ndarray | None = None  # previous chunk's withheld
                                                   # tail (float samples) for
                                                   # the crossfade join; live
                                                   # only inside _speak_chunked
        self._setup_cancel: threading.Event | None = None  # fresh per start(); stop() sets it
        self._backend = "builtin"   # fixed in start() from config.local_backend
        self._whisper = None        # faster-whisper model, lazily loaded + cached
        self._llama = None          # llama_cpp.Llama (builtin backend), cached
        # GAPS-22 (B-V8): the turn profile starts UNDECIDED (None),
        # never silently "quick" — the mic sets it per turn from
        # classify_turn_profile; the builtin escalate sites read it
        # through the classifier's accessor (see
        # local_client_classifier.ENTRY_PROFILE).
        self._active_profile = None
        self._kokoro_child = None   # KokoroChild (isolated Kokoro subprocess, B-R1-3s), cached
        self._last_tts_rung: str | None = None  # B-R1-3s: the TTS rung that spoke last ("kokoro"/"piper"/"sapi"), None when none did
        self._kokoro_blend_warned = False  # B-R1-3s: the blend-drop note prints once per session
        self._piper_voice = None    # PiperVoice, lazily loaded + cached
        self._piper_voice_path: str | None = None
        self._no_tts_noted = False
        # v5.49 (programme item 36): lite-tier plan/goal gate.
        # _lite_plan_confirmed latches True once the user has said
        # yes to running multi-step work on the small brain this
        # session (warn-once); confirm_lite_plan is the injectable
        # yes/no seam — assign any sync or async callable taking
        # the warning text and returning truthy for yes. It
        # defaults to _default_confirm_lite_plan (conservative:
        # the confirm_gate popup when a handler is registered,
        # otherwise no — never a silent yes).
        self._lite_plan_confirmed = False
        self.confirm_lite_plan: Callable[[str], Any] = (
            self._default_confirm_lite_plan)

    @property
    def client(self) -> None:
        """No genai client exists on the local engine (brain.client contract)."""
        return None

    def _notify_status(self, status: str):
        if self.on_status_change:
            self.on_status_change(status)

    # ------------------------------------------------------------------ #
    # Session lifecycle
    # ------------------------------------------------------------------ #
    async def start(self, audio_manager):
        """Connects to the local brain and runs the mic loop until stop().

        Backend preparation differs (Ollama reachability vs built-in
        asset download + in-process model load); the session tail —
        mic loop, keep-alive, DISCONNECTED — is shared verbatim.
        """
        injection.clear_taint()  # fresh session, fresh trust state (v5.3)
        _bump_epoch("local start")  # v5.48 (programme item 20)
        self._notify_status("CONNECTING")
        self._backend = (config.local_backend or "builtin").strip().lower()
        # Fresh cancel handle for this connect's asset setup; stop()
        # sets it so a disconnect mid-download pauses the downloads
        # (their .part files resume on the next connect).
        self._setup_cancel = threading.Event()

        if self._backend == "ollama":
            if not await self._check_ollama():
                return  # start() returns, mirroring the MISSING_API_KEY path
            # GAPS-22 (B-V5): the model actually serving — the
            # Ollama model name the reachability gate just checked.
            self.connected_model = config.ollama_model
        else:
            if not await self._prepare_builtin():
                return
            # GAPS-22 (B-V5): the model actually serving — the
            # effective file the load path just resolved and
            # loaded, not the config pick (which may name a file
            # that is not what loaded). Set only after the prep
            # succeeded, so a failed load leaves None.
            self.connected_model = str(local_assets.model_path())

        await self._run_session(audio_manager)

    async def _check_ollama(self) -> bool:
        """The v1.7 Ollama reachability gate: True when the server
        answers /api/tags. On failure the offline ERROR status is
        emitted here and start() returns without a session."""
        loop = asyncio.get_running_loop()
        try:
            tags = await loop.run_in_executor(None, self._fetch_tags)
        except Exception:  # noqa: BLE001 — any failure here means "offline"
            self._notify_status("ERROR: Local brain offline — is Ollama running?")
            return False

        names = [m.get("name", "") for m in tags.get("models", [])]
        if config.ollama_model not in names:
            # Not fatal: the user may pull the model mid-session.
            print(f"[LocalBrain] Model '{config.ollama_model}' is not in Ollama's "
                  f"model list {names}. Pull it with: ollama pull {config.ollama_model}")
        return True

    async def _prepare_builtin(self) -> bool:
        """Zero-setup preparation for the built-in backend: download any
        missing assets (progress surfaces as SETUP: statuses), preload
        Whisper, then load the GGUF into llama.cpp. Returns False —
        after emitting an ERROR status — when the brain can't start."""
        loop = asyncio.get_running_loop()

        def forward(line: str):
            # Download progress reads as SETUP: statuses; failures are
            # surfaced from ensure's return value as a proper ERROR below.
            if not line.startswith("ERROR:"):
                self._notify_status("SETUP: " + line)

        lines = await loop.run_in_executor(
            None, local_assets.ensure_local_assets, forward, self._setup_cancel
        )
        setup_cancelled = (
            self._setup_cancel is not None and self._setup_cancel.is_set()
        ) or any("Download paused" in line for line in lines)
        if setup_cancelled:
            # stop() fired mid-setup (or the downloads were cancelled):
            # the .part files stay on disk and resume next connect.
            self._notify_status(
                "ERROR: Setup paused — it will resume next time you connect."
            )
            return False
        errors = [line for line in lines if line.startswith("ERROR:")]
        if errors:
            # v5.25: a failed VOICE download must never kill the
            # brain. Kokoro is one voice in a chain (Kokoro → Piper
            # → Windows SAPI → text-only — see _speak), so when the
            # brain model itself is on disk, a voice-only failure
            # (the Kokoro model / voice bank lines) degrades the
            # voice and the session proceeds. Before this, ANY
            # download error aborted the whole connect — a GitHub
            # hiccup on the 325 MB voice file read to the user as
            # "the brain is not working". When something else also
            # failed, the BRAIN's error is the one worth surfacing,
            # not the voice's.
            def _is_voice_error(line: str) -> bool:
                low = line.lower()
                return ("kokoro" in low or "voice engine" in low
                        or "voice bank" in low)

            brain_on_disk = False
            try:
                brain_on_disk = bool(local_assets.model_ready())
            except Exception:  # noqa: BLE001 — readiness must not crash setup
                brain_on_disk = False
            if brain_on_disk and all(_is_voice_error(e) for e in errors):
                print(f"[LocalBrain] Voice download failed, continuing "
                      f"with the fallback voice: {errors[0]}")
                self._notify_status(
                    "SETUP: Her premium voice didn't download — she'll "
                    "use the backup voice for now.")
            else:
                fatal = next(
                    (e for e in errors if not _is_voice_error(e)),
                    errors[0])
                self._notify_status(fatal)
                return False

        # Instrumented warm-up (v5.22, brain audit 2 #2): each load
        # announces itself BEFORE it starts, so the GUI's "Warming
        # up…" label always names the phase actually in progress
        # instead of sitting unexplained while Whisper and the GGUF
        # load with no timeout and no word.
        self._notify_status("SETUP: Loading her ears (speech recognition)…")
        await loop.run_in_executor(None, self._ensure_whisper)

        try:
            self._notify_status(
                "SETUP: Loading her brain — the first load can take a "
                "few minutes…"
            )
            await loop.run_in_executor(None, self._ensure_llama)
        except Exception as e:  # noqa: BLE001 — no model, no session
            print(f"[LocalBrain] Built-in model failed to load: {e}")
            self._notify_status(f"ERROR: {str(e)[:300]}")
            return False
        return True

    def _ensure_whisper(self) -> bool:
        """Loads (and caches) the faster-whisper model. Sync — executor
        only; the first load downloads the weights. Returns False, with
        a console note, when the ears are unavailable: the session then
        degrades to typed input instead of dying.

        GAPS-16: the model comes from the one shared STT factory
        (core/voice/asr.get_whisper_model) — the same construction
        this method used to perform itself (the effective model
        argument from local_assets.whisper_model_arg(), device
        auto, compute_type int8), now shared with the lite session
        and the meeting path instead of loaded once per session.
        The load exception itself is printed by the factory."""
        if self._whisper is not None:
            return True
        try:
            from .asr import (
                faster_whisper_available as _fw_available,
                get_whisper_model as _get_whisper_model,
            )
        except Exception:  # noqa: BLE001
            print("[LocalBrain] faster-whisper is not installed — voice input "
                  "is disabled (typed input still works). Install it with: "
                  "pip install faster-whisper")
            return False
        if not _fw_available():
            print("[LocalBrain] faster-whisper is not installed — voice input "
                  "is disabled (typed input still works). Install it with: "
                  "pip install faster-whisper")
            return False
        self._whisper = _get_whisper_model()
        if self._whisper is None:
            print("[LocalBrain] Whisper model failed to load")
            return False
        return True

    def _ensure_llama(self):
        """Loads (and caches) the built-in GGUF via llama-cpp-python.
        Sync — executor only. Raises on failure: start() turns that
        into an ERROR status, because without a model there is no
        brain at all (unlike the ears, this one is not degradable)."""
        if self._llama is not None:
            return
        from llama_cpp import Llama

        # GPU first (v1.9): a CUDA/Vulkan llama-cpp build offloads every
        # layer with n_gpu_layers=-1 and the 4B brain flies. A CPU-only
        # wheel may raise on the unknown setting (or ignore it) — any
        # exception retries with the CPU-safe 0, and the console note
        # says which mode actually engaged.
        model_file = str(local_assets.model_path())
        n_ctx = local_assets.effective_n_ctx()
        flash_noted = False

        def _build(n_gpu_layers: int):
            """One Llama construction, flash attention first (v4.1):
            flash_attn shrinks the attention memory traffic, but an
            older/odd llama-cpp-python build may not accept the
            kwarg — a TypeError retries the SAME construction
            without it (one console note), so a flash-attn quirk
            can never block the brain from loading."""
            nonlocal flash_noted
            try:
                return Llama(
                    model_path=model_file,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    flash_attn=True,
                    verbose=False,
                )
            except TypeError:
                if not flash_noted:
                    flash_noted = True
                    print("[LocalBrain] flash attention unavailable in "
                          "this llama build — continuing without")
                return Llama(
                    model_path=model_file,
                    n_ctx=n_ctx,
                    n_gpu_layers=n_gpu_layers,
                    verbose=False,
                )

        model_name = Path(model_file).name
        if _gpu_load_suspect(model_name):
            # The previous run died (or was killed) mid-GPU-load of
            # this exact model — skip the GPU attempt this run, load
            # on the CPU, and say why. A successful load here clears
            # the marker to "ready" like any other, so a fixed driver
            # earns the GPU another chance next launch (v5.22 #3).
            self._notify_status(
                "SETUP: Last time the graphics-card load didn't "
                "finish — loading her brain on the processor instead…"
            )
            print("[LocalBrain] GPU load marker found — CPU-first this run")
            self._llama = _build(0)
            print("[LocalBrain] CPU mode (n_gpu_layers=0)")
            _set_active_tokenizer(self._llama)  # v5.43 (B3)
            _write_llama_load_state("ready", model_name)
            return
        _write_llama_load_state("gpu_loading", model_name)
        try:
            self._llama = _build(-1)
            print("[LocalBrain] GPU offload on (n_gpu_layers=-1)")
        except Exception as e:  # noqa: BLE001 — fall back, never fail the load
            print(f"[LocalBrain] GPU offload unavailable ({e}) — CPU mode")
            self._llama = _build(0)
            print("[LocalBrain] CPU mode (n_gpu_layers=0)")
        _set_active_tokenizer(self._llama)  # v5.43 (B3)
        _write_llama_load_state("ready", model_name)

    # ------------------------------------------------------------------ #
    # Ambient context (v5.44, N4) + warm-standby preload (v5.44, B2)
    # ------------------------------------------------------------------ #
    def _seed_ambient_context(self):
        """Session start (N4): pull the ambient packet and stash
        it for the FIRST turn's prompt assembly. An empty packet
        stashes nothing, so empty-packet prompts stay
        byte-identical to before. Never raises."""
        packet = _ambient_packet_text()
        self._ambient_packet = packet
        if packet:
            self._ambient_context_pending = packet

    def _record_turn_actions(self, actions: list[dict[str, Any]]) -> None:
        """Turn-truthfulness (6th-B08): append one turn's action
        records to the session's trace, capped at the last 20 turns
        so a long session cannot grow it without bound. Never
        raises: tracing is bookkeeping, never a turn-breaker."""
        try:
            trace = getattr(self, "_turn_action_trace", None)
            if trace is None:
                trace = self._turn_action_trace = []
            trace.append({"actions": [dict(a) for a in actions]})
            del trace[:-20]
        except Exception:  # noqa: BLE001 — tracing never breaks a turn
            pass

    def _previous_turn_action_note(self) -> str:
        """The latest recorded turn's actions as one system-prompt
        note, or "" when no turn has recorded actions yet. The note
        rides the system prompt (never the dialogue), so the model
        can ground a later "what did you do?" in what actually ran
        — including failures and rejections, not just successes."""
        try:
            trace = getattr(self, "_turn_action_trace", None) or []
            if not trace:
                return ""
            summary = _format_action_trace(
                trace[-1].get("actions") or [])
            if not summary:
                return ""
            return (
                "Actions from the previous turn, for reference when "
                "asked what was done (statuses are what the tool "
                "calls reported — succeeded, failed, or rejected "
                "before running): " + summary)
        except Exception:  # noqa: BLE001 — tracing never breaks a turn
            return ""

    def _take_ambient_block(self) -> str:
        """The stashed ambient packet, exactly once: the next
        turn to assemble its prompt takes it (a refresh via
        set_ambient_context re-stashes for the turn after)."""
        packet = getattr(self, "_ambient_context_pending", "") or ""
        self._ambient_context_pending = ""
        if packet:
            self._ambient_packet = packet
        return packet

    def set_ambient_context(self, packet: str):
        """BrainSession.refresh_ambient_context's local seam
        (N4): stash a changed packet to fold into the next
        turn's assembly — nothing is emitted or spoken here."""
        packet = packet or ""
        if packet:
            self._ambient_packet = packet
            self._ambient_context_pending = packet

    def preload_model_only(self) -> bool:
        """B2 warm standby (v5.44): load the brain MODEL only —
        no session start, no mic, no audio, no speech. Sync —
        executor only. Builtin backend only, and only when the
        brain file is actually on disk (a standby must never
        start a multi-GB download or the full _prepare_builtin
        sequence). The loaded model caches on this instance
        exactly as _ensure_llama always caches it: if this
        session later wins the fallback, start() reuses it; if
        it is discarded, the cache dies with the instance.
        Returns True when a model is resident afterwards."""
        backend = (config.local_backend or "builtin").strip().lower()
        if backend == "ollama":
            return False  # the model lives in the Ollama server
        try:
            if not local_assets.brain_ready():
                return False
            self._ensure_llama()
        except Exception as e:  # noqa: BLE001 — standby is best-effort
            print(f"[LocalBrain] Standby preload failed: {e}")
            return False
        return self._llama is not None

    async def _run_session(self, audio_manager):
        """The shared session tail both backends run once prepared."""
        self._audio_manager = audio_manager
        self._running = True
        self.is_connected = True
        # A freshly-started session is active by definition — the idle
        # clocks start now, not whenever the object was constructed.
        self._last_brain_activity = time.monotonic()
        self._last_stt_activity = time.monotonic()
        self._last_utterance_at = time.monotonic()
        # Continuity (v5.43, B10): a returning user is greeted by a
        # brain that remembers yesterday — seed the history and the
        # first-turn day block before the first turn can assemble.
        self._seed_continuity_history()
        # Ambient context (v5.44, N4): the NOSE packet is stashed
        # for the first turn's assembly, at the memory block's place.
        self._seed_ambient_context()
        self._notify_status("LISTENING")

        mic_task = asyncio.create_task(self._mic_loop(audio_manager))
        housekeeping_task = asyncio.create_task(self._housekeeping_loop())
        self._session_tasks = [mic_task, housekeeping_task]
        try:
            # Keep-alive: like Gemini's gather over its send/receive tasks,
            # start() returns only when the session ends.
            await asyncio.gather(*self._session_tasks)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            print(f"[LocalBrain] Session error: {e}")
            self._notify_status(f"ERROR: {str(e)[:300]}")
        finally:
            self.is_connected = False
            self._running = False
            _bump_epoch("local session end")  # v5.48 (programme item 20)
            self._notify_status("DISCONNECTED")

    def stop(self):
        self._running = False
        _bump_epoch("local stop")  # v5.48 (programme item 20)
        # B-R1-3s: the session's Kokoro child dies with the
        # session — never a zombie after stop(). Best-effort;
        # stop() must not fail on it.
        try:
            self._close_kokoro_child()
        except Exception:  # noqa: BLE001 — teardown must not raise
            pass
        if self._setup_cancel is not None:
            # Pause any in-flight first-run downloads; their partial
            # .part files are resume state and stay on disk.
            self._setup_cancel.set()
        # v5.32: wake the worker loop to deliver these cancels
        # (core/taskwake.py) — bare cancel() from the GUI thread
        # can sit undelivered in a parked loop.
        for task in self._session_tasks:
            cancel_waking(task)
        for task in list(self._turn_tasks):
            cancel_waking(task)

    # ------------------------------------------------------------------ #
    # Idle memory management (v3.2): a VYUK left connected all day
    # must not hold the brain + ears in RAM forever. After
    # effective_idle_unload_s() without a brain turn (or an
    # utterance), the
    # housekeeping loop drops the session's references and gc's; the
    # next turn reloads through the existing lazy paths
    # (_ensure_llama with a SETUP line, _ensure_whisper silently).
    # Unloading NEVER happens mid-turn or mid-speech: the check runs
    # only while the turn lock is free and _speaking is False — and
    # _llama_chat / _transcribe capture their own reference before
    # calling into the model, so even a call that started a heartbeat
    # earlier finishes against a live object.
    # ------------------------------------------------------------------ #
    async def _housekeeping_loop(self):
        """Every HOUSEKEEPING_TICK_S, offers the idle unload a chance
        (in an executor — gc.collect() can take a moment). Lives and
        dies with the session: stop() cancels it with the mic task."""
        loop = asyncio.get_running_loop()
        while self._running:
            await asyncio.sleep(HOUSEKEEPING_TICK_S)
            if not self._running:
                break
            try:
                await loop.run_in_executor(None, self._maybe_idle_unload)
            except Exception as e:  # noqa: BLE001 — housekeeping must never kill the session
                print(f"[LocalBrain] Housekeeping pass failed: {e}")

    def _maybe_idle_unload(self):
        """Drops the llama (builtin backend) and/or the Whisper model
        after effective_idle_unload_s() of quiet (IDLE_UNLOAD_S on
        roomy PCs, 5 minutes on ≤10 GB ones — v4.1). Sync — executor
        only. See the section header for the mid-turn safety
        argument."""
        if self._speaking or self._turn_lock.locked():
            return
        idle_s = local_assets.effective_idle_unload_s()
        idle_min = int(idle_s // 60)
        now = time.monotonic()
        freed = False
        if (self._backend == "builtin" and self._llama is not None
                and now - self._last_brain_activity > idle_s):
            _clear_active_tokenizer(self._llama)  # v5.43 (B3)
            self._llama = None
            self._llama_unloaded = True
            freed = True
            print(f"[LocalBrain] Brain idle for {idle_min}+ minutes — "
                  "model unloaded to free RAM; it reloads on the "
                  "next message.")
        if (self._whisper is not None
                and now - self._last_stt_activity > idle_s):
            # GAPS-16: the model is the shared STT factory's instance
            # — releasing the factory entry with it, so the unload
            # actually frees the RAM (a factory that kept the
            # instance would make this unload a no-op). Identity-
            # checked: a caller holding the model keeps using its
            # own instance, and the next load constructs again.
            try:
                from .asr import release_whisper_model as _release

                _release(self._whisper)
            except Exception:  # noqa: BLE001 — the release never breaks the unload
                pass
            self._whisper = None
            freed = True
            print(f"[LocalBrain] Ears idle for {idle_min}+ minutes — "
                  "Whisper unloaded to free RAM; it reloads on the "
                  "next utterance.")
        if freed:
            gc.collect()

    def release_model(self) -> None:
        """Drop the local model refs (GAPS-22, B-V6).

        The standby-discard path calls this on a never-started
        session: the llama (with its active tokenizer cleared)
        and the Whisper model (released through the shared STT
        factory, so the release actually frees the RAM), then
        ``gc.collect()``. The same drops ``_maybe_idle_unload``
        performs, without its idle gates — a standby has no
        turns in flight, so there is nothing to protect — and
        with ``_llama_unloaded`` set, so the reload path knows
        the model is gone. Never raises.
        """
        try:
            if self._llama is not None:
                _clear_active_tokenizer(self._llama)
                self._llama = None
                self._llama_unloaded = True
        except Exception:  # noqa: BLE001 — the release never breaks the discard
            pass
        try:
            if self._whisper is not None:
                from .asr import release_whisper_model as _release

                _release(self._whisper)
                self._whisper = None
        except Exception:  # noqa: BLE001 — the release never breaks the discard
            pass
        gc.collect()

    # ------------------------------------------------------------------ #
    # Ollama plumbing (sync helpers — always called via run_in_executor)
    # ------------------------------------------------------------------ #
    def _fetch_tags(self) -> dict[str, Any]:
        req = urllib.request.Request(f"{config.ollama_host}/api/tags")
        with urllib.request.urlopen(req, timeout=OLLAMA_TAGS_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _ollama_chat(self, messages: list[dict[str, Any]],
                     ollama_tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "model": config.ollama_model,
            "messages": messages,
            "tools": ollama_tools,
            "stream": False,
        }
        req = urllib.request.Request(
            f"{config.ollama_host}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=OLLAMA_CHAT_TIMEOUT_S) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        # MN-2.8: zero-cost ledger row (exact when Ollama
        # returned eval counts, estimated otherwise).
        _cost_record_local(result, config.ollama_model,
                           _cost_prompt_text(messages),
                           str(((result.get("message") or {}).get("content")
                                or "")) if isinstance(result, dict) else "")
        return result
