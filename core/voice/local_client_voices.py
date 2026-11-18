"""Local-live session mixin: TTS engines: Kokoro / Piper / SAPI synthesis and voice resolution.

Batch 5 (skills split, local_client closer): moved verbatim
from core/voice/local_client.py; methods resolve self-calls
through the composed LocalLiveSession class.

B-R1-3s (2026-11-09) — the session TTS path runs Kokoro ONLY
through the isolated child process (core/desktop/kokoro_isolate.py
+ kokoro_child.py): the main process holds NO kokoro-onnx import
here any more. Before this batch _kokoro_synthesize built a
Kokoro ONNX session in-process (from kokoro_onnx import Kokoro)
and _synthesize_kokoro / _speak_kokoro presence-checked it with
a bare `import kokoro_onnx` — so the LIVE voice path ran GPL-3.0
code (phonemizer + espeak-ng, through kokoro-onnx) in-process on
every utterance, even after B-R1-3r isolated the driver wrapper.
The session path is what actually speaks, so this rewire is what
closes the hard block.

LOPHOLE FAMILY — "the live voice path bypassing the isolation":
a presence check that becomes a load; a child failure that falls
to Piper silently (the isolation is bypassed exactly when it
matters); a session-held Kokoro session the boundary cannot see
or reset; a child that outlives the session (a zombie).

ONE design: the session routes ALL Kokoro synthesis through the
SAME boundary (KokoroChild.synthesize) — this module holds NO
kokoro-onnx import (enforced by the AST test in
tests/test_kokoro_isolate.py: after this batch the ONLY
kokoro_onnx importer in the tree is kokoro_child.py); a child
failure is an honest failure (this method returns None) and the
fallback chain records the rung that spoke
(core/voice/local_client_speech.py _speak_via_chain /
_last_tts_rung), never a silent Piper swap; the session's Kokoro
session IS the boundary's child (reset = kill + respawn); the
child dies with the session (close in stop(), plus the stdin-EOF
death watch + atexit backstops in the boundary).

Voice blend (v5.43, M4): the child protocol carries the voice
NAME only — a blended style tensor cannot cross it
(kokoro_child.py validates `voice` as a string, and the protocol
is frozen by B-R1-3r). A configured blend therefore renders the
named base voice through the child, and the dropped blend is
recorded honestly (a [LocalBrain] line, once per session) — the
voice identity is preserved, only the style mix is dropped. The
blend is default-off (_configured_blend returns None when
unconfigured), so this is a cold path.

Rejected alternatives (recorded):
* Leaving the session path in-process with a comment saying
  "isolated" (a comment is not a boundary).
* A session path that silently falls to Piper on a child
  failure (a licence-gated fallback presented as the default).
* A session-held Kokoro session that survives a child kill
  (a poisoned session).
* A child that outlives the session (a zombie).
* Resolving the blend tensor in the main process from the
  voices bin (np.load, data not GPL code) — the tensor still
  cannot cross the frozen child protocol, so the blend would
  be computed and then unusable; the named voice + honest
  record is the faithful shape inside the freeze.
"""

import asyncio
import io
import numpy as np
import sys
import wave
from . import (local_assets)
from ..config import (DATA_DIR, config)
from ..languages import (kokoro_voice_for_text)
from .local_client_pcm import (_configured_blend, _kokoro_speed, _loudness_enabled, _normalize_pcm, _normalize_samples, _trim_chunk_pcm, _utterance_loudness_pcm, kokoro_lang_for_voice)
from pathlib import (Path)


class LocalLiveSessionVoicesMixin:
    async def _synthesize_kokoro(self, text: str,
                                 speed_mult: float = 1.0) -> bytes | None:
        """One chunk of Kokoro PCM, or None when Kokoro can't speak it
        (assets missing / package absent / synthesis failed).
        `speed_mult` is the per-unit pace multiplier (v5.17: 0.92 for
        an emphasized span, 1.0 otherwise) — composed into the Kokoro
        speed inside _kokoro_synthesize."""
        if not local_assets.kokoro_ready():
            return None
        # B-R1-3s: presence is probed through the boundary (a
        # throwaway child interpreter, cached) — this process
        # never imports kokoro_onnx, not even for a check.
        from ..desktop import kokoro_isolate as _isolate

        if not _isolate.probe_engine()["available"]:
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._kokoro_synthesize, text, speed_mult)

    async def _synthesize_piper(self, text: str,
                                speed_mult: float = 1.0) -> bytes | None:
        """One chunk of Piper PCM, or None when Piper can't speak it.
        `speed_mult` is accepted for a uniform synth signature and
        deliberately unused: Piper keeps whole-sentence behaviour
        and emphasis has no pacing effect there (v5.17, M14)."""
        voice_path = Path(DATA_DIR) / "voices" / config.local_tts_voice
        if not voice_path.exists():
            # v5.35: the fallback voice is provisioned like
            # every other asset (the bundled install copy
            # first, then the copy setup downloads into
            # data/voices) — it used to be a file only a
            # manual drop could provide, so this rung of the
            # chain silently never existed on a fresh install.
            try:
                resolved, _cfg = local_assets.piper_paths()
                if resolved.exists():
                    voice_path = resolved
            except Exception:  # noqa: BLE001 — a resolver fault must not mute her
                pass
        if not voice_path.exists():
            return None
        try:
            import piper  # noqa: F401 — presence check, as in _speak_piper
        except ImportError:
            return None
        loop = asyncio.get_running_loop()
        pcm = await loop.run_in_executor(
            None, self._piper_synthesize, text, str(voice_path)
        )
        if pcm and self._backend != "ollama":
            # v5.51 M-3: trim the chunk's ragged silence edges
            # (pad kept) before assembly — see _trim_chunk_pcm.
            pcm = _trim_chunk_pcm(pcm)
            if _loudness_enabled():
                # v5.51 M-2: the per-chunk v5.1 loudness pass is
                # skipped — the post-assembly RMS loudness
                # standard (_utterance_loudness_pcm, applied once
                # per assembled utterance) is now the only gain.
                pass
            else:
                # Mouth craft (v5.1): builtin-chain Piper chunks get the
                # same loudness pass Kokoro gets at synthesis, so a reply
                # that falls back mid-chain doesn't change volume. The
                # Ollama backend's audio stays byte-identical.
                pcm = _normalize_pcm(pcm)
        return pcm

    async def _speak_kokoro(self, text: str) -> bool:
        """Kokoro via the isolated child process — the built-in
        backend's first voice. Only attempted when the
        auto-downloaded assets are on disk."""
        if not local_assets.kokoro_ready():
            return False
        # B-R1-3s: presence is probed through the boundary (a
        # throwaway child interpreter, cached) — this process
        # never imports kokoro_onnx, not even for a check.
        from ..desktop import kokoro_isolate as _isolate

        if not _isolate.probe_engine()["available"]:
            return False

        loop = asyncio.get_running_loop()
        pcm = await loop.run_in_executor(None, self._kokoro_synthesize, text)
        if not pcm:
            return False
        # v5.51 M-2: the whole-text utterance is already assembled
        # (one synthesis), so the RMS loudness standard applies
        # here, once — the choke point for non-chunked Kokoro.
        pcm = _utterance_loudness_pcm([pcm])[0]
        await self._play_pcm(pcm)
        return True

    # -- the isolated Kokoro child (B-R1-3s) -------------------------
    def _kokoro_child_for_session(self, _isolate):
        """The session's persistent KokoroChild, created lazily.
        Returns None when it cannot be created (never raises) —
        the caller then reports Kokoro as unavailable, exactly
        as a missing engine. Created here, in the executor
        thread that synthesizes; KokoroChild() itself is cheap
        (the child spawns lazily inside synthesize())."""
        child = getattr(self, "_kokoro_child", None)
        if child is not None:
            return child
        try:
            child = _isolate.KokoroChild()
        except Exception as e:  # noqa: BLE001 — the reason IS the report
            print(f"[LocalBrain] Kokoro child could not be created: {e}")
            return None
        self._kokoro_child = child
        return child

    def _close_kokoro_child(self) -> None:
        """Kill the session's Kokoro child, if any (session
        teardown — called from stop()). Best-effort, never
        raises. A synthesize in flight on the executor thread
        sees the dead child as an honest failure, never a crash:
        the boundary kills the child on any failure and the
        next call would respawn — but after stop() there is no
        next call, the session is over."""
        child = getattr(self, "_kokoro_child", None)
        self._kokoro_child = None
        if child is None:
            return
        try:
            child.close()
        except Exception:  # noqa: BLE001 — teardown must not raise
            pass

    def _kokoro_synthesize(self, text: str,
                           speed_mult: float = 1.0) -> bytes | None:
        """Blocking Kokoro synthesis → 24 kHz int16 PCM (executor only).
        B-R1-3s: the engine runs ONLY in the isolated child
        process (core/desktop/kokoro_isolate.py + kokoro_child.py)
        — this method holds NO kokoro-onnx import and builds NO
        in-process ONNX session. One JSON request in (text, voice,
        lang, speed, paths), one float32 render out; a child
        failure is an honest None (the caller falls through to
        the next rung, which records the rung that spoke — never
        a silent Piper swap).
        Kokoro emits float32 samples at 24 kHz natively — exactly our
        playback rate — so conversion is a scale-and-clip. The pace
        composes (v5.17): config.kokoro_speed (clamped at read time —
        see _kokoro_speed) × the session multiplier (the speech
        override, else config.briefing_speed — see
        _session_speed_mult) × `speed_mult`, the per-unit emphasis
        multiplier (0.92 for an emphasized span, else 1.0). The
        composed speed is capped at 4.0 — the child protocol's
        own ceiling (its _validate refuses anything above it);
        the composed product is positive by construction (every
        factor is positive), so only the top needs the cap.
        The float samples are loudness-normalized before conversion (v5.1), so
        Kokoro is the one place both the chunked stream and the
        whole-text path get the same level.
        Automatic voice switching (v5.41, Kokoro path only): with
        config.voice_auto_switch on, the utterance's voice follows
        the reply's detected language (core.languages) and the
        synthesis language follows that voice; English, short or
        ambiguous text keeps the configured home voice. With the
        toggle off this is exactly the pre-v5.41 behaviour. The
        Piper and SAPI rungs never switch.
        Voice blend (v5.43, M4): a configured blend CANNOT cross
        the child protocol — the child validates `voice` as a
        string name, and the protocol is frozen (B-R1-3r). A
        configured blend therefore renders the named base voice
        through the child, and the dropped blend is recorded
        honestly (once per session, below) — the voice identity
        is preserved, only the style mix is dropped. The blend
        is default-off, so this is a cold path."""
        if not isinstance(text, str) or not text.strip():
            return None
        try:
            from ..desktop import kokoro_isolate as _isolate

            child = self._kokoro_child_for_session(_isolate)
            if child is None:
                return None

            voice = config.kokoro_voice
            if getattr(config, "voice_auto_switch", False):
                voice = kokoro_voice_for_text(text, voice)
            blend = _configured_blend()
            if blend is not None and not getattr(
                    self, "_kokoro_blend_warned", False):
                self._kokoro_blend_warned = True
                print(f"[LocalBrain] Kokoro voice blend {blend[0]!r} "
                      f"({blend[1]:.2f}) cannot cross the isolated "
                      "child protocol (voice is a name there, not a "
                      "tensor) — the named voice speaks instead "
                      f"({voice!r}).")
            model_path, voices_path = local_assets.kokoro_paths()
            out = child.synthesize({
                "text": text, "voice": voice,
                "speed": min(4.0, _kokoro_speed()
                             * self._session_speed_mult() * speed_mult),
                "lang": kokoro_lang_for_voice(voice),
                "model_path": str(model_path),
                "voices_path": str(voices_path),
            })
            if not out["ok"]:
                print(f"[LocalBrain] Kokoro synthesis failed: "
                      f"{out['reason']}")
                return None
            samples = np.asarray(out["samples"], dtype=np.float32).ravel()
            rate = int(out["rate"])
            if rate != config.sample_rate_out and samples.size:
                target = int(samples.size * config.sample_rate_out / rate)
                samples = np.interp(
                    np.linspace(0, samples.size, target, endpoint=False),
                    np.arange(samples.size),
                    samples,
                )
            if _loudness_enabled():
                # v5.51 M-2: the per-chunk v5.1 loudness pass is
                # skipped — the post-assembly RMS loudness
                # standard (_utterance_loudness_pcm, applied once
                # per assembled utterance) is now the only gain.
                pass
            else:
                samples = _normalize_samples(samples)
            pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
            # v5.51 M-3: trim the chunk's ragged silence edges
            # (pad kept) before assembly — see _trim_chunk_pcm.
            # (This method serves only the builtin chain — the
            # Ollama backend has no Kokoro rung — so no backend
            # gate is needed here.)
            return _trim_chunk_pcm(pcm.tobytes())
        except Exception as e:  # noqa: BLE001
            print(f"[LocalBrain] Kokoro synthesis failed: {e}")
            return None

    async def _speak_piper(self, text: str) -> bool:
        voice_path = Path(DATA_DIR) / "voices" / config.local_tts_voice
        if not voice_path.exists():
            return False
        try:
            import piper  # noqa: F401 — presence check; real load is in _piper_synthesize
        except ImportError:
            return False

        loop = asyncio.get_running_loop()
        pcm = await loop.run_in_executor(
            None, self._piper_synthesize, text, str(voice_path)
        )
        if not pcm:
            return False
        if self._backend != "ollama":
            # v5.51 M-3: trim the utterance's ragged silence edges
            # (pad kept) — see _trim_chunk_pcm.
            pcm = _trim_chunk_pcm(pcm)
            if _loudness_enabled():
                # v5.51 M-2: the whole-text utterance is already
                # assembled (one synthesis), so the RMS loudness
                # standard applies here, once — the choke point
                # for non-chunked Piper.
                pcm = _utterance_loudness_pcm([pcm])[0]
            else:
                pcm = _normalize_pcm(pcm)  # v5.1 loudness — see _synthesize_piper
        await self._play_pcm(pcm, text_len=len(text))
        return True

    def _piper_synthesize(self, text: str, voice_path: str) -> bytes | None:
        """Blocking Piper synthesis → 24 kHz int16 PCM (executor only)."""
        try:
            from piper import PiperVoice

            if self._piper_voice is None or self._piper_voice_path != voice_path:
                self._piper_voice = PiperVoice.load(voice_path)
                self._piper_voice_path = voice_path

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_out:
                self._piper_voice.synthesize(text, wav_out)
            buf.seek(0)
            with wave.open(buf, "rb") as wav_in:
                rate = wav_in.getframerate()
                frames = wav_in.readframes(wav_in.getnframes())

            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
            if rate != config.sample_rate_out and samples.size:
                target = int(samples.size * config.sample_rate_out / rate)
                samples = np.interp(
                    np.linspace(0, samples.size, target, endpoint=False),
                    np.arange(samples.size),
                    samples,
                )
            return samples.astype(np.int16).tobytes()
        except Exception as e:  # noqa: BLE001
            print(f"[LocalBrain] Piper synthesis failed: {e}")
            return None

    async def _speak_sapi(self, text: str) -> bool:
        if sys.platform != "win32":
            return False
        try:
            import pyttsx3  # noqa: F401 — presence check
        except ImportError:
            return False
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sapi_speak, text)
        return True

    @staticmethod
    def _sapi_speak(text: str):
        import pyttsx3

        engine = pyttsx3.init()
        try:
            engine.say(text)
            engine.runAndWait()
        finally:
            engine.stop()

    @property
    def voice_ready(self) -> bool:
        """Phase 1 (C05): the STT/voice-readiness signal, distinct
        from is_connected.

        True only when the faster-whisper ears are actually
        loaded in this session (self._whisper set by
        _ensure_whisper). Before this, _prepare_builtin awaited
        _ensure_whisper and discarded the result, so a session
        with dead ears degraded to typed input with no signal
        anywhere saying voice was down. Note the difference from
        local_assets.voice_ready(): that reports whether the
        whisper weights are PRESENT on disk; this reports
        whether they are LOADED in this session. A session can
        be fully connected (is_connected True) while this is
        False — that split is exactly what C05 is about."""
        return self._whisper is not None

