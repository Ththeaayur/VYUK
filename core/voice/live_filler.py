"""P2 speed pass (2026-11-16): the filler bridge.

When the brain genuinely needs longer than the half-second
budget, VYUK says something in the gap ("On it.", "Checking.")
instead of going silent — the plan's rule, not a habit. The
bridge fires at most ONCE per turn, only after the budget has
already been missed, only while the turn is genuinely waiting
(user spoke and no model audio has arrived, or a tool batch is
running), and never while the model is speaking. The moment
model audio arrives, any filler still queued is cut short.

Two honest limits, stated here, not hidden:
  * The filler is spoken by the LOCAL voice (Piper), not the
    Gemini Live voice — there is no way to render the model's
    own voice locally. It is a short bridge phrase, not a
    reply, and it is never logged as one (no transcript bubble,
    no memory entry — only an Activity log line from the
    caller). The field test judges whether the voice switch
    feels wrong.
  * Rendering needs Piper (the local chain's fallback rung) and
    its voice file. Where Piper is absent the bridge is
    UNAVAILABLE, not broken: it records the reason in status()
    and never fires, so a machine without Piper simply gets
    silence past the budget — exactly as today. The Kokoro
    child is deliberately NOT used here: spawning a child
    process for a two-word bridge is disproportionate, and the
    isolated-child boundary belongs to the session that owns
    it. If Piper is absent on the field machine, the Kokoro
    pre-render is the named follow-up.

Rendering is a SYNC callable returning 24 kHz int16 PCM bytes
or None; the default is render_filler_pcm below (Piper,
mirroring core/voice/local_client_voices._piper_synthesize).
Tests inject a fake render, so the bridge logic is fully
testable where no TTS engine exists.
"""

from __future__ import annotations

import io
import time
import wave
from typing import Any, Callable

import numpy as np

# The bridge phrases, in rotation order. Short, plain, no
# content — a bridge is not a reply, and it must never sound
# like one started.
FILLER_PHRASES = ("On it.", "Checking.", "One moment.")

# Fire only once the half-second budget is already missed, with
# a small margin so a reply landing just on the budget is never
# talked over.
FILLER_GAP_S = 0.6

# Tick the watchdog this often (the live session's filler
# watchdog task sleeps this long between checks).
FILLER_TICK_S = 0.1

_PIPER_VOICE = None
_PIPER_VOICE_PATH: str | None = None


def render_filler_pcm(text: str) -> bytes | None:
    """One filler phrase as 24 kHz int16 PCM via Piper, or None
    when Piper (or its voice file) is unavailable. Sync and
    blocking — callers run it in an executor, never on the
    receive loop. Never raises: an unavailable engine is a
    capability note, not an error."""
    try:
        from ..config import config, DATA_DIR
        from . import local_assets
    except Exception:  # noqa: BLE001 — no config, no render
        return None
    try:
        from pathlib import Path
        voice_path = Path(DATA_DIR) / "voices" / config.local_tts_voice
        if not voice_path.exists():
            try:
                resolved, _cfg = local_assets.piper_paths()
                if resolved.exists():
                    voice_path = resolved
            except Exception:  # noqa: BLE001 — resolver fault mutes nothing
                pass
        if not voice_path.exists():
            return None
        try:
            from piper import PiperVoice
        except ImportError:
            return None
        global _PIPER_VOICE, _PIPER_VOICE_PATH
        if (_PIPER_VOICE is None
                or _PIPER_VOICE_PATH != str(voice_path)):
            _PIPER_VOICE = PiperVoice.load(str(voice_path))
            _PIPER_VOICE_PATH = str(voice_path)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_out:
            _PIPER_VOICE.synthesize(text, wav_out)
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
    except Exception:  # noqa: BLE001 — render failure is "no filler"
        return None


class FillerBridge:
    """One live session's filler state.

    Driven by the session: note_voice() on user speech,
    note_model_audio() on the server's first audio chunk,
    arm_tools() when a tool batch starts, tick() from the
    session's filler watchdog (every FILLER_TICK_S). The
    bridge decides; the watchdog only calls tick().

    `audio_manager` is the session's AudioManager (enqueue /
    begin_sentence / skip_current_sentence / is_muted /
    playback_paused / is_playing). It may be replaced after
    construction (the session re-creates its manager on
    reconnect); set the attribute directly.
    """

    def __init__(self, audio_manager=None,
                 render: Callable[[str], bytes | None] | None = None,
                 clock=None, gap_s: float = FILLER_GAP_S):
        self.audio_manager = audio_manager
        self._render = render or render_filler_pcm
        self._clock = clock or time.monotonic
        self._gap_s = float(gap_s)
        self._cache: dict[str, bytes | None] = {}
        self._last_voice_at: float | None = None
        self._model_audio_at: float | None = None
        self._tools_since: float | None = None
        self._fired_phrase: str | None = None
        self._attempted_this_turn = False
        self._last_phrase: str | None = None
        self._render_available: bool | None = None
        self._disabled_reason: str | None = None

    # -- session events ---------------------------------------------
    def note_voice(self, at: float | None = None) -> None:
        """User voice heard: a new turn is starting. A fired
        filler belongs to the old turn — clear it, and clear
        the model-audio stamp with it."""
        now = self._clock() if at is None else at
        self._last_voice_at = now
        self._model_audio_at = None
        self._fired_phrase = None
        self._attempted_this_turn = False
        self._tools_since = None

    def note_model_audio(self, at: float | None = None) -> None:
        """The server's audio arrived: the wait is over. Any
        filler still queued is cut short — the model's words
        win over the bridge, immediately. The per-wait state
        clears with the wait, so a LATER wait in the same turn
        (a second tool batch) gets its own filler chance."""
        now = self._clock() if at is None else at
        self._model_audio_at = now
        self._tools_since = None
        if self._fired_phrase is not None:
            self._cut_filler()
        self._fired_phrase = None
        self._attempted_this_turn = False

    def arm_tools(self, at: float | None = None) -> None:
        """A tool batch started: the turn is waiting on tools
        now, even if no fresh voice was heard (a model-asked
        tool round). A new wait clears the model-audio stamp
        of the wait before it — the audio that arrived is
        done, the tools are what is waited on now."""
        self._model_audio_at = None
        if self._tools_since is None:
            self._tools_since = self._clock() if at is None else at

    def note_tool_results(self, at: float | None = None) -> None:
        """The tool results were delivered: the tool wait is
        over. Without this the arm would go stale and the next
        tick could speak a filler into a wait that already
        ended."""
        self._tools_since = None

    def cancel(self) -> None:
        """The user barged in (or the session ended): cut a
        playing filler and clear the wait. The PCM cache and
        the rotation memory survive."""
        self._cut_filler()
        self._last_voice_at = None
        self._model_audio_at = None
        self._tools_since = None
        self._fired_phrase = None
        self._attempted_this_turn = False

    def reset(self) -> None:
        """Per-turn state cleared (turn complete / session end).
        The PCM cache and the rotation memory survive — a new
        turn reuses the rendered phrases."""
        self._last_voice_at = None
        self._model_audio_at = None
        self._tools_since = None
        self._fired_phrase = None
        self._attempted_this_turn = False

    # -- the decision ------------------------------------------------
    def _waiting_since(self) -> float | None:
        if self._tools_since is not None:
            return self._tools_since
        return self._last_voice_at

    def due(self, now: float | None = None) -> bool:
        """Pure predicate (tests drive this directly): the turn
        has waited past the gap with no model audio, no filler
        has fired this turn, and nothing forbids speaking."""
        now = self._clock() if now is None else now
        if self._fired_phrase is not None:
            return False
        if self._attempted_this_turn:
            return False
        if self._render_available is False:
            return False
        if self._model_audio_at is not None:
            return False
        waiting_since = self._waiting_since()
        if waiting_since is None:
            return False
        if now - waiting_since < self._gap_s:
            return False
        am = self.audio_manager
        if am is None:
            return False
        if bool(getattr(am, "is_muted", False)):
            return False
        if bool(getattr(am, "playback_paused", False)):
            return False
        # Never talk over her own still-draining reply: the
        # previous turn's audio must have finished first.
        if bool(getattr(am, "is_playing", False)):
            return False
        return True

    def _next_phrase(self) -> str:
        for phrase in FILLER_PHRASES:
            if phrase != self._last_phrase:
                return phrase
        return FILLER_PHRASES[0]

    def _render_cached(self, phrase: str) -> bytes | None:
        if phrase not in self._cache:
            try:
                self._cache[phrase] = self._render(phrase)
            except Exception:  # noqa: BLE001 — render fault is "no filler"
                self._cache[phrase] = None
        return self._cache[phrase]

    def _cut_filler(self) -> None:
        """Drop the rest of the filler currently playing. The
        filler was enqueued as one tracked sentence, so the
        head record is the filler — skip_current_sentence
        drops exactly it. Best-effort: a manager without the
        API never breaks the turn."""
        am = self.audio_manager
        if am is None:
            return
        skip = getattr(am, "skip_current_sentence", None)
        if callable(skip):
            try:
                skip()
            except Exception:  # noqa: BLE001 — cutting is best-effort
                pass

    async def tick(self, now: float | None = None) -> str | None:
        """One watchdog tick: fire the bridge phrase when due.
        Returns the phrase spoken, or None. Rendering runs in
        the executor (it blocks); playback is a plain enqueue
        on the session's audio manager. Never raises."""
        now = self._clock() if now is None else now
        if not self.due(now):
            return None
        phrase = self._next_phrase()
        self._attempted_this_turn = True
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            pcm = await loop.run_in_executor(
                None, self._render_cached, phrase)
        except Exception:  # noqa: BLE001 — no loop / render fault
            pcm = None
        if not pcm:
            # No engine, or this phrase would not render: the
            # bridge is unavailable. The render cache holds the
            # None, so this turn does not retry every tick and
            # later turns do not re-probe either (engines do
            # not appear mid-session); the flag below is what
            # status() reports.
            self._render_available = False
            self._disabled_reason = ("no filler renderer: Piper "
                                     "unavailable")
            return None
        # The render took time: the model may have answered
        # while it ran. Never speak over a reply that arrived.
        if self._model_audio_at is not None:
            return None
        self._render_available = True
        am = self.audio_manager
        try:
            blocks = am.enqueue_playback(pcm)
            begin = getattr(am, "begin_sentence", None)
            if callable(begin):
                begin(blocks, 1, 1, phrase)
        except Exception:  # noqa: BLE001 — enqueue fault is "no filler"
            return None
        self._fired_phrase = phrase
        self._last_phrase = phrase
        return phrase

    def status(self) -> dict[str, Any]:
        """What the Doctor / status surface reads: whether the
        bridge can speak at all, and why not when it cannot."""
        return {
            "available": self._render_available,
            "disabled_reason": self._disabled_reason,
            "gap_s": self._gap_s,
            "armed": self._waiting_since() is not None,
            "fired_this_turn": self._fired_phrase,
            "phrases": list(FILLER_PHRASES),
        }
