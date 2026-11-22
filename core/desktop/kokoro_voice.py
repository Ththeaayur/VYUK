"""B-R1-3 (2026-11-09) Kokoro TTS voice — the ONE dict-returning
wrapper a driver calls to speak text in a named Kokoro voice.

THE GAP (proved first, file/line evidence)
------------------------------------------
Kokoro is ALREADY the shipped voice, and the machinery around it
is extensive — this batch builds only the remainder:

* ``core/voice/local_assets_kokoro.py`` (68 lines) resolves the
  Kokoro asset pair (kokoro-v1.0.onnx ~310 MB + voices-v1.0.bin
  ~27 MB, operator-downloaded, never bundled in the repo) and
  ``kokoro_ready()`` reports only that both files exist and clear
  a size floor (model > 1 MB, voices > 100 KB). It never says
  whether a NAMED voice is in the voices bin.
* ``core/voice/local_client_voices.py`` (343 lines) synthesizes:
  ``_kokoro_synthesize`` (Kokoro via kokoro-onnx, 24 kHz, auto
  voice-switch, blend, loudness, trim), ``_speak_kokoro``,
  ``_synthesize_kokoro``, plus the Piper/SAPI rungs.
* ``core/voice/local_client_speech.py:132-134`` runs the fallback
  chain — builtin: (Kokoro, Piper, SAPI); Ollama: (Piper, SAPI)
  — by trying each rung until one returns True. No record is kept
  of WHICH rung spoke: a Kokoro failure falls to Piper Amy
  silently, and a Piper failure falls to SAPI silently.
* ``core/languages.py`` holds the curated voice table (9
  languages: en-us/am_michael, en-gb/bm_george, hi/hm_omega,
  ja/jf_alpha, cmn/zm_yunjian, es/em_alex, fr-fr/ff_siwis,
  it/im_nicola, pt-br/pm_alex), ``kokoro_voice_for_text`` (the
  auto-switch rule), ``set_preferred_language`` (the one write
  path for the home language + voice), and ``reply_language_
  directive``. The default home is English (GB) / bm_george
  (``config.kokoro_voice`` default "bm_george",
  ``config.preferred_language`` default "en-gb"); the Piper Amy
  file is ``config.local_tts_voice`` default
  "en_US-amy-medium.onnx".
* ``kokoro_lang_for_voice`` (``core/voice/local_client_pcm.py``)
  derives a voice's synthesis language from its FIRST LETTER
  ("b" -> en-gb, "h" -> hi, ...), defaulting unknown voices to
  "en-us" — a heuristic, not a lookup, and it never refuses.

What is genuinely missing, and what this module adds:

* NO dict-returning driver wrapper exists: the TTS path is async
  session methods returning bool / bytes-or-None. No single call
  says "speak this text in this language with this voice" and
  returns a dict with voice / language / audio / verification.
* NO voice-availability check exists: nothing checks whether a
  NAMED voice (e.g. "hm_omega") is in the voices bin before
  synthesis. A missing voice makes ``Kokoro.create()`` raise,
  which ``_kokoro_synthesize`` catches and returns None — and
  the chain then falls to Piper, silently.
* NO voice/language mismatch refusal exists: a caller can ask
  for bm_george with Hindi text and the engine will read it in
  the wrong voice.
* NO render verification exists: nothing checks the rendered
  audio for length / clipping / silence before it is handed to
  playback. ``_trim_chunk_pcm`` trims silence edges and
  ``_normalize_samples`` normalizes level, but neither verifies
  the render is sane.
* The fallback is SILENT: the chain never names the rung that
  spoke, so the driver cannot tell Kokoro from Piper Amy from
  SAPI — and Piper is a licence-gated fallback (see below),
  not a silent default.

This module is the driver surface. It does NOT replace the
session's TTS path, does NOT change the default/home voice
(English George), does NOT change the onboarding language
preference, does NOT touch the Piper Amy understudy, and does
NOT wire the voice loop (Stage 1 hold stands).

LOPHOLE FAMILY — "the wrong voice speaks, or no voice speaks,
or the voice sounds wrong"
---------------------------------------------------------------
* A voice that is missing (the requested voice file is absent,
  so the engine silently falls back to another voice).
* A voice that is wrong for the language (an English voice
  reading Hindi, or a Hindi voice reading English).
* A voice that sounds wrong (the render was never verified —
  the voice is grainy, clipped, or the wrong speed).
* A fallback that is silent (Kokoro unavailable, so Piper Amy
  speaks without anyone saying so — a licence-gated fallback
  presented as the default).

ONE design closes the class: every spoken reply goes through
this module; the wrapper takes the text + the language + the
voice identity and runs the real Kokoro engine; a missing voice
is an honest failure (never a silent fallback to another voice);
a voice/language mismatch is refused (never read aloud in the
wrong voice); every render is verified (the output audio is
checked for length, clipping, and silence before it is handed
to playback — a render that fails verification is an honest
failure, never played); the Piper Amy fallback is EXPLICIT (it
is named in the result as the fallback, with the licence flag,
never presented as Kokoro, and never invoked by this module —
the driver decides).

Rejected alternatives (recorded):
* A silent fallback to another voice (the user hears a voice
  they did not choose). (A missing voice is a failure naming
  the voice and the voices present.)
* Reading in the wrong voice (the language and the voice
  disagree). (The mismatch is refused before synthesis.)
* Playing an unverified render (grainy/clipped audio reaches
  his ear). (Every render is verified; a failed render is
  never returned as audio.)
* A silent Piper swap (a licence-gated fallback presented as
  the default). (The fallback is named in every result, with
  its availability and licence flag; this module never calls
  Piper itself.)
* Deriving the language from the voice's first letter alone
  (the tree's ``kokoro_lang_for_voice`` heuristic). (The
  curated table in ``core.languages`` is the authority for the
  nine curated voices; the prefix rule is only the fallback
  for non-curated voices in the bin.)
* Silent resampling of a non-24 kHz render (a resampled render
  is different audio; the wrapper must not silently hand the
  driver audio at a rate it did not ask for). (A render at any
  rate other than 24000 is refused.)

VOICE + LANGUAGE RULES
-----------------------
* Voice identity is the voice NAME ("hm_omega", "bm_george").
  An explicit voice wins; otherwise the language's curated
  voice wins; otherwise the auto-switch rule
  (``kokoro_voice_for_text``) wins; otherwise the home voice
  (``config.kokoro_voice``).
* Language identity is the synthesis code ("hi", "en-gb"). An
  explicit language wins; otherwise the voice's language
  (curated table, then prefix rule); otherwise the home
  language (``config.preferred_language``).
* A voice/language mismatch is refused: the voice's language
  must equal the requested language, with en-us and en-gb
  treated as one English (``ENGLISH_CODES`` in languages.py —
  the same rule the switcher uses).
* A voice that is not in the voices bin is refused before any
  synthesis is attempted.
* Text is capped at 10,000 characters (the driver chunks longer
  replies first); empty or whitespace-only text is refused.
* Speed must be a finite number in (0, 4.0]; bools are refused.

SAMPLE-RATE RULE
----------------
* Kokoro emits 24 kHz natively (kokoro-onnx SAMPLE_RATE =
  24000; the tree's ``config.sample_rate_out`` is 24000). The
  wrapper returns 24 kHz int16 PCM. A render at any other rate
  is refused, never silently resampled.

RENDER VERIFICATION (every render, before it is returned)
---------------------------------------------------------
* Non-empty (at least one sample).
* All samples finite (no NaN/inf).
* Rate is exactly 24000.
* Duration > 0 s and <= 3600 s (a sanity cap, not a pace rule).
* Not silent: peak |x| > 1e-3.
* Not pinned at the rail: the fraction of samples with |x| >=
  0.9999 must be <= 0.5 (more than half the samples at the rail
  is a broken render, not a loud one).
* The verification result (peak, clipped fraction, duration,
  sample count) rides in the returned dict — the driver sees
  what was checked, not just that it passed.

DEPENDENCY (proposed — parent rules)
------------------------------------
* NO new dependency. kokoro-onnx 0.6.1 is already declared
  (requirements.txt:183 ``kokoro-onnx>=0.4``, lock pin 0.6.1,
  THIRD_PARTY row 25 MIT — wrapper licence read from the
  installed wheel's LICENSE file, "MIT License, Copyright (c)
  2025 github.com/thewh1teagle", 2026-11-09) and already
  shipped. The wrapper adds ZERO new weight: the model
  (~310 MB) and voices (~27 MB) are operator-downloaded assets
  already in the tree's asset system (local_assets_kokoro.py),
  not pip packages.
* kokoro-onnx 0.6.1 hard-requires (unconditional Requires-Dist,
  read from the PyPI JSON + the installed METADATA, 2026-11-09):
  espeakng-loader>=0.2.4, numpy>=2.0.2, onnxruntime>=1.20.1,
  phonemizer>=3.4.0. onnxruntime is already declared (THIRD_PARTY
  row 26, MIT). The phoneme path ran IN-PROCESS on every Kokoro
  utterance before B-R1-3r: kokoro_onnx/tokenizer.py imports
  phonemizer and calls ``phonemizer.phonemize()`` (espeak
  backend) for every synthesis. Since B-R1-3r (2026-11-09) the
  whole engine runs ONLY in the isolated child process
  (``core/desktop/kokoro_isolate.py`` + ``kokoro_child.py``):
  text in via a JSON line, PCM out via a JSON line, nothing
  else crosses; the main process holds NO kokoro-onnx import
  (enforced by a source-grep test, tests/test_kokoro_isolate.py).
  The session TTS path (``core/voice/local_client_voices.py``)
  still synthesizes in-process — NOT rewired in B-R1-3r, a
  separate parent decision; it is the named residual.
* PHONEMIZER LICENCE, re-read 2026-11-09 — PARENT RULING
  2026-11-09 (GPL-3.0 confirmed): phonemizer 3.4.0 is GPL-3.0
  (installed METADATA "License: GNU GENERAL PUBLIC LICENSE";
  LICENSE file is the GPL-3.0 text; PyPI classifier "License ::
  OSI Approved :: GNU General Public License v3 or later
  (GPLv3+)"). B-R1-3 ADDED the phonemizer row (row 121); B-R1-3r
  isolated the engine in a child process. The espeak-ng GPL-3.0
  position is unchanged (row 120: bundled libespeak-ng,
  dynamically loaded, GPL-3.0 text in the installer + written
  source offer).
* espeakng-loader 0.2.4's OWN licence could NOT be verified from
  its wheel or PyPI metadata (no License field, no LICENSE file
  in the dist-info, no PyPI classifiers) — recorded honestly as
  unverified. What it bundles IS known: libespeak-ng.so +
  espeak-ng-data (21 MB installed), whose GPL-3.0 is already
  recorded (row 120).
* Kokoro-82M model licence: Apache-2.0 (THIRD_PARTY row 22,
  already recorded; confirmed 2026-11-09 from the Hugging Face
  model card + repo licence field). The voices file ships with
  the model under the same Apache-2.0.
* LAZY-imported (function-level static imports, the sympy
  pattern); absence of the engine degrades every call to an
  honest engine_unavailable, nothing else.
"""

import time

__all__ = [
    "engine_available",
    "engine_status",
    "voice_status",
    "fallback_info",
    "speak",
    "reset",
    "set_backend",
    "reset_backend",
    "KokoroSpeaker",
    "SAMPLE_RATE",
    "MAX_TEXT_CHARS",
    "MAX_RENDER_S",
    "SILENCE_PEAK",
    "CLIP_FRACTION_LIMIT",
]

# -- constants ---------------------------------------------------------
SAMPLE_RATE = 24000  # Kokoro native rate; config.sample_rate_out too
MAX_TEXT_CHARS = 10_000  # the driver chunks longer replies first
MAX_RENDER_S = 3600.0  # sanity cap on a single render, not a pace rule
SILENCE_PEAK = 1e-3  # a render quieter than this is silence, not speech
CLIP_FRACTION_LIMIT = 0.5  # > half the samples at the rail = broken
SPEED_MIN = 0.0  # exclusive
SPEED_MAX = 4.0  # inclusive

# The Piper Amy fallback, named explicitly everywhere it appears.
# Licence flag is the standing record (brief, B-R1-3): original
# rhasspy/piper was MIT but ARCHIVED 2025-10-06; active development
# is OHF-Voice/piper1-gpl under GPL-3.0 — needs a licence decision
# at build time, cannot be assumed MIT. This module never calls
# Piper itself; it only names the fallback and its availability.
FALLBACK_ENGINE = "piper"
FALLBACK_LICENCE = (
    "GPL-3.0 — piper1-gpl (OHF-Voice); the original rhasspy/piper "
    "was MIT but archived 2025-10-06. Licence decision owed; "
    "never a silent default."
)

_ENGLISH = frozenset({"en-us", "en-gb"})


class _EngineUnavailable(Exception):
    """The Kokoro engine (kokoro-onnx, or the tree's voice
    modules under it) is not importable in this interpreter."""


# -- lazy engine import (sympy pattern) ---------------------------------
# Two layers, deliberately split: _load_tree() imports only the
# tree's own voice modules (languages, config, asset paths) — the
# pieces voice/language resolution needs, which work on any
# interpreter. The engine itself is NEVER imported here: since
# B-R1-3r the Kokoro engine runs only in the isolated child
# process (core/desktop/kokoro_isolate.py + kokoro_child.py),
# reached through a subprocess boundary — there is no
# in-process kokoro-onnx import left in this module.
_TREE = None  # cached dict of tree names, or False when unavailable


def _load_tree():
    """Import the tree's Kokoro-adjacent modules once; return the
    names resolution needs. Raises _EngineUnavailable when they
    cannot be imported."""
    global _TREE
    if _TREE is None:
        try:
            import numpy as _np
            from ..voice import local_assets as _assets
            from ..languages import (
                ENGLISH_CODES as _EnglishCodes,
            )
            from ..languages import (
                LANGUAGES as _Languages,
            )
            from ..languages import (
                kokoro_voice_for_text as _voice_for_text,
            )
            from ..languages import (
                language_by_code as _language_by_code,
            )
            from ..voice.local_client_pcm import (
                kokoro_lang_for_voice as _lang_for_voice,
            )
            from ..config import config as _config
        except Exception as e:  # noqa: BLE001 — the reason IS the report
            _TREE = False
            raise _EngineUnavailable(
                "the tree's voice modules (languages / config / "
                f"local_assets) are not importable ({e})"
            ) from e
        _TREE = {
            "numpy": _np,
            "assets": _assets,
            "english_codes": _EnglishCodes,
            "languages": _Languages,
            "voice_for_text": _voice_for_text,
            "language_by_code": _language_by_code,
            "lang_for_voice": _lang_for_voice,
            "config": _config,
        }
    if _TREE is False:
        raise _EngineUnavailable(
            "the tree's voice modules (languages / config / "
            "local_assets) are not importable in this interpreter"
        )
    return _TREE


# -- render verification -------------------------------------------------
def _verify_render(samples, rate: int) -> dict:
    """Verify one raw float render. Returns {ok, reason, peak,
    clipped_fraction, duration_s, sample_count}. The checks are
    the objective ones: empty, non-finite, wrong rate, silent,
    pinned at the rail. A loud render is not a failed render —
    only a render pinned at the rail is."""
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32).ravel()
    n = int(arr.size)
    if n == 0:
        return {"ok": False, "reason": "render is empty (no samples)",
                "peak": 0.0, "clipped_fraction": 0.0,
                "duration_s": 0.0, "sample_count": 0}
    if not bool(np.isfinite(arr).all()):
        return {"ok": False,
                "reason": "render holds non-finite samples (NaN/inf)",
                "peak": 0.0, "clipped_fraction": 0.0,
                "duration_s": 0.0, "sample_count": n}
    if rate != SAMPLE_RATE:
        return {"ok": False,
                "reason": (f"render rate is {rate}, not {SAMPLE_RATE} "
                           "(Kokoro emits 24 kHz; a render at another "
                           "rate is refused, never silently resampled)"),
                "peak": float(np.max(np.abs(arr))),
                "clipped_fraction": 0.0,
                "duration_s": round(n / SAMPLE_RATE, 3),
                "sample_count": n}
    duration = n / SAMPLE_RATE
    peak = float(np.max(np.abs(arr)))
    clipped = float(np.mean(np.abs(arr) >= 0.9999))
    if duration <= 0.0 or duration > MAX_RENDER_S:
        return {"ok": False,
                "reason": (f"render duration {duration:.2f} s is outside "
                           f"(0, {MAX_RENDER_S:.0f}] s"),
                "peak": peak, "clipped_fraction": clipped,
                "duration_s": round(duration, 3), "sample_count": n}
    if peak <= SILENCE_PEAK:
        return {"ok": False,
                "reason": (f"render is silent (peak {peak:.6f} <= "
                           f"{SILENCE_PEAK}) — nothing was spoken"),
                "peak": peak, "clipped_fraction": clipped,
                "duration_s": round(duration, 3), "sample_count": n}
    if clipped > CLIP_FRACTION_LIMIT:
        return {"ok": False,
                "reason": (f"render is pinned at the rail "
                           f"({clipped:.0%} of samples >= 0.9999) — "
                           "a broken render, not a loud one"),
                "peak": peak, "clipped_fraction": clipped,
                "duration_s": round(duration, 3), "sample_count": n}
    return {"ok": True, "reason": "",
            "peak": peak, "clipped_fraction": clipped,
            "duration_s": round(duration, 3), "sample_count": n}


def _to_int16_pcm(samples) -> bytes:
    """Float32 samples in [-1, 1] -> int16 PCM bytes. The scale is
    the dtype's own range (B-R1-1 lesson): float samples scale by
    32767, never by a constant meant for another dtype. Values
    outside [-1, 1] are clipped first, and the clipped fraction is
    reported by _verify_render — this conversion never hides it."""
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32).ravel()
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767).astype(np.int16).tobytes()


# -- real backend (isolated Kokoro child process, B-R1-3r) --------------
class _RealBackend:
    """Thin adapter over the isolated Kokoro child process
    (core/desktop/kokoro_isolate.py). The Kokoro instance (ONNX
    session + tokenizer, which imports phonemizer + espeak-ng)
    is built in the CHILD, lazily on first speak — never at
    import, never by a status call, and never in this process:
    this module holds NO kokoro-onnx import. Availability is
    probed by a throwaway child interpreter
    (``kokoro_isolate.probe_engine``), so the real backend fails
    fast with engine_unavailable when the package is absent.
    The voices list is read from the voices bin alone (np.load,
    no session) and cached."""

    def __init__(self):
        # kokoro-onnx importability is checked here (in a child
        # process, never this one) so the real backend fails fast
        # with engine_unavailable when the package is absent. The
        # child itself is still spawned lazily in speak(), and
        # voices() never spawns one.
        from . import kokoro_isolate as _isolate

        eng = _load_tree()
        probe = _isolate.probe_engine()
        if not probe["available"]:
            raise _EngineUnavailable(
                "the Kokoro engine (kokoro-onnx) is not importable "
                f"in the child interpreter ({probe['reason']}); speech "
                "degrades to engine_unavailable"
            )
        self._eng = eng
        self._child = None  # lazy KokoroChild; spawned on first speak
        self._voices = None  # cached tuple of voice names, or None

    @property
    def backend(self) -> str:
        return "kokoro-onnx"

    def _model_paths(self):
        return self._eng["assets"].kokoro_paths()

    def voices(self):
        """Voice names in the voices bin, or None when the bin
        cannot be read. Never builds a session."""
        if self._voices is not None:
            return self._voices
        try:
            _model, voices_path = self._model_paths()
            np = self._eng["numpy"]
            arr = np.load(str(voices_path), allow_pickle=False)
            names = arr.dtype.names
            self._voices = tuple(names) if names else ()
        except Exception:  # noqa: BLE001 — an unreadable bin is "no voices"
            self._voices = None
        return self._voices

    def speak(self, text, voice, speed, lang):
        """Render one text through the isolated child process.
        Returns (samples, rate). The child is spawned here, on
        first speak only; a child failure raises, and the wrapper
        reports it as an honest failure — never a silent
        in-process fallback (there is no in-process path left)."""
        from . import kokoro_isolate as _isolate

        if self._child is None:
            self._child = _isolate.KokoroChild()
        model_path, voices_path = self._model_paths()
        out = self._child.synthesize({
            "text": text, "voice": voice, "speed": speed, "lang": lang,
            "model_path": str(model_path),
            "voices_path": str(voices_path),
        })
        if not out["ok"]:
            raise RuntimeError(out["reason"])
        return out["samples"], out["rate"]

    def reset(self) -> None:
        # Kill the child (a replaced model or voices file is
        # picked up next time) and drop the voices list.
        if self._child is not None:
            try:
                self._child.close()
            except Exception:  # noqa: BLE001 — reset must not raise
                pass
            self._child = None
        self._voices = None


# -- validation + resolution ---------------------------------------------
def _refuse(reason):
    return {"ok": False, "reason": reason}


def _voice_language(eng, voice: str) -> str:
    """The synthesis language for a voice name: the curated table
    first (the authority for the nine curated voices), then the
    tree's prefix rule for non-curated voices in the bin."""
    for entry in eng["languages"]:
        if entry.voice == voice:
            return entry.code
    return eng["lang_for_voice"](voice)


def _resolve(eng, text, language, voice, auto_switch: bool):
    """Resolve (voice, language) for one speak call. Returns
    (voice, language) or (None, error-reason). An explicit voice
    wins; otherwise the language's curated voice; otherwise the
    auto-switch rule; otherwise the home voice. An explicit
    language wins; otherwise the voice's language; otherwise the
    home language. A mismatch between an explicit voice and an
    explicit language is a refusal, never a guess."""
    config = eng["config"]
    home_voice = str(getattr(config, "kokoro_voice", "bm_george"))
    home_lang = str(getattr(config, "preferred_language", "en-gb"))
    if language is not None:
        entry = eng["language_by_code"](language)
        if entry is None:
            return None, (f"unknown language {language!r} — the curated "
                          "languages are en-us, en-gb, hi, ja, cmn, es, "
                          "fr-fr, it, pt-br")
        language = entry.code
    if voice is None:
        if language is not None:
            voice = eng["language_by_code"](language).voice
        elif auto_switch:
            voice = eng["voice_for_text"](text, home_voice)
        else:
            voice = home_voice
    if language is None:
        language = _voice_language(eng, voice)
    else:
        voice_lang = _voice_language(eng, voice)
        same = (voice_lang == language
                or (voice_lang in eng["english_codes"]
                    and language in eng["english_codes"]))
        if not same:
            return None, (f"voice {voice!r} speaks {voice_lang}, not "
                          f"{language} — a voice/language mismatch is "
                          "refused, never read aloud in the wrong voice")
    return (voice, language), None


# -- the wrapper ---------------------------------------------------------
class KokoroSpeaker:
    """One Kokoro speaker. ``backend=None`` builds the real engine
    (lazy import; engine_unavailable when the engine cannot
    import). A test backend may be injected; the wrapper's
    validation, resolution, and verification rules are identical
    either way.

    A test backend implements ``voices()`` returning a tuple of
    voice names (or None) and ``speak(text, voice, speed, lang)``
    returning (samples, rate), plus ``reset()`` — the same shape
    as _RealBackend.
    """

    def __init__(self, backend=None):
        self._backend = backend  # None -> build the real one lazily

    # -- internals ------------------------------------------------------
    def _ensure_backend(self):
        if self._backend is None:
            self._backend = _RealBackend()
        return self._backend

    @staticmethod
    def _backend_name(backend) -> str | None:
        return getattr(backend, "backend", None)

    def _engine_or_none(self):
        """The backend, or None + the engine_unavailable dict
        when the engine cannot load."""
        try:
            return self._ensure_backend(), None
        except _EngineUnavailable as e:
            return None, {"ok": False, "reason": "engine_unavailable",
                          "detail": str(e)}

    def _fallback(self, eng=None):
        """The Piper Amy fallback, named explicitly with its
        availability and licence flag. This module never calls
        Piper itself — the driver decides, with the facts."""
        voice = "en_US-amy-medium.onnx"
        available = False
        reason = "unavailable"
        try:
            if eng is not None:
                config = eng["config"]
                voice = str(getattr(config, "local_tts_voice", voice))
                from ..voice import local_assets as assets
                from pathlib import Path as _Path
                from ..config import DATA_DIR as _DATA_DIR
                # Same order as the session's _synthesize_piper: the
                # configured voice file first, then the resolver.
                candidate = _Path(_DATA_DIR) / "voices" / voice
                if candidate.exists():
                    available = True
                else:
                    resolved, _cfg = assets.piper_paths()
                    available = bool(resolved.exists())
                reason = (None if available else
                          f"Piper voice file {voice!r} not on disk")
        except Exception as e:  # noqa: BLE001 — fallback facts are best-effort
            reason = f"fallback check failed: {e}"
        try:
            import piper  # noqa: F401
            piper_ok = True
        except Exception:  # noqa: BLE001
            piper_ok = False
        if available and not piper_ok:
            available = False
            reason = "Piper voice file present but the piper package is absent"
        return {
            "engine": FALLBACK_ENGINE,
            "voice": voice,
            "available": available,
            "licence": FALLBACK_LICENCE,
            "reason": reason,
        }

    def _report(self, ok, voice, language, reason="", **extra):
        out = {
            "ok": ok,
            "voice": voice,
            "language": language,
            "sample_rate": None,
            "duration_s": 0.0,
            "samples": 0,
            "audio": None,
            "verification": None,
            "fallback": None,
            "reason": reason,
        }
        out.update(extra)
        return out

    # -- public API ------------------------------------------------------
    def speak(self, text, *, language=None, voice=None, speed: float = 1.0,
              auto_switch: bool = True,
              timestamp: float | None = None) -> dict:
        """Speak one text in one voice. Returns a dict with
        ok/voice/language/audio/verification/fallback; see the
        module docstring for the voice, language, rate, and
        verification rules.

        A validation refusal never touches the engine. A missing
        voice, a mismatch, or a failed verification is an honest
        failure — never a silent fallback, never unverified audio.
        """
        if timestamp is None:
            timestamp = time.monotonic()
        try:
            timestamp = float(timestamp)
        except (TypeError, ValueError):
            return self._report(
                False, voice, language,
                reason="timestamp must be a number (monotonic seconds)")
        if timestamp != timestamp:  # NaN
            return self._report(
                False, voice, language,
                reason="timestamp must not be NaN")

        # 1. Validation: text, language, voice, speed. None of this
        #    touches the engine.
        if not isinstance(text, str):
            return self._report(
                False, voice, language,
                reason="text must be a string — turn-taking reads "
                       "what was said, not a number or an object")
        if not text.strip():
            return self._report(
                False, voice, language,
                reason="text is empty (nothing to speak)")
        if len(text) > MAX_TEXT_CHARS:
            return self._report(
                False, voice, language,
                reason=(f"text is {len(text)} characters (max "
                        f"{MAX_TEXT_CHARS}) — chunk it first"))
        if language is not None and not isinstance(language, str):
            return self._report(
                False, voice, language,
                reason="language must be a synthesis code string (or None)")
        if voice is not None and (
                not isinstance(voice, str) or not voice.strip()):
            return self._report(
                False, voice, language,
                reason="voice must be a voice name string (or None)")
        if isinstance(speed, bool) or not isinstance(speed, (int, float)):
            return self._report(
                False, voice, language,
                reason="speed must be a number")
        speed = float(speed)
        if speed != speed or speed <= SPEED_MIN or speed > SPEED_MAX:
            return self._report(
                False, voice, language,
                reason=(f"speed must be in ({SPEED_MIN}, {SPEED_MAX}] "
                        f"(got {speed})"))

        # 2. Engine (lazy). Without it, nothing can be rendered.
        #    A test backend needs no kokoro-onnx import; the real
        #    backend fails here when the package is absent.
        backend, failure = self._engine_or_none()
        if failure is not None:
            eng = None
            try:
                eng = _load_tree()
            except _EngineUnavailable:
                eng = None
            return self._report(
                False, voice, language, reason="engine_unavailable",
                detail=failure["detail"],
                fallback=self._fallback(eng),
                timestamp=timestamp)
        eng = _load_tree()

        # 3. Resolve voice + language (mismatch is a refusal).
        resolved, err = _resolve(eng, text, language, voice, auto_switch)
        if err is not None:
            return self._report(
                False, voice, language, reason=err,
                fallback=self._fallback(eng), timestamp=timestamp)
        voice, language = resolved

        # 4. Voice availability: the name must be in the bin.
        try:
            present = backend.voices()
        except Exception as e:  # noqa: BLE001 — a voices-list fault is a failure
            return self._report(
                False, voice, language,
                reason=f"the voices bin could not be read: {e}",
                fallback=self._fallback(eng), timestamp=timestamp)
        if present is None:
            return self._report(
                False, voice, language,
                reason="the voices bin could not be read (no voices "
                       "present to check against)",
                fallback=self._fallback(eng), timestamp=timestamp)
        if voice not in present:
            return self._report(
                False, voice, language,
                reason=(f"voice {voice!r} is not in the voices bin "
                        f"({len(present)} voices present) — a missing "
                        "voice is an honest failure, never a silent "
                        "fallback to another voice"),
                fallback=self._fallback(eng), timestamp=timestamp)

        # 5. Render (one engine call) + verify before returning audio.
        try:
            samples, rate = backend.speak(text, voice, speed, language)
        except Exception as e:  # noqa: BLE001 — engine error: honest failure
            return self._report(
                False, voice, language,
                reason=f"the Kokoro engine failed while rendering: {e}",
                fallback=self._fallback(eng), timestamp=timestamp)
        verification = _verify_render(samples, rate)
        if not verification["ok"]:
            return self._report(
                False, voice, language, reason=verification["reason"],
                sample_rate=rate,
                duration_s=verification["duration_s"],
                samples=verification["sample_count"],
                verification=verification,
                fallback=self._fallback(eng), timestamp=timestamp)
        return self._report(
            True, voice, language, reason="",
            sample_rate=SAMPLE_RATE,
            duration_s=verification["duration_s"],
            samples=verification["sample_count"],
            audio=_to_int16_pcm(samples),
            verification=verification,
            fallback=self._fallback(eng),
            timestamp=timestamp)

    def reset(self) -> dict:
        """Drop the cached engine (a replaced model or voices file
        is picked up next time). Returns a dict; never raises."""
        if self._backend is not None:
            try:
                self._backend.reset()
            except Exception:  # noqa: BLE001 — reset must not raise
                pass
            self._backend = None
        return self._report(True, None, None, reason="")

    @property
    def state(self) -> str:
        """The speaker has no held state (each speak is one render);
        this exists so drivers can ask uniformly."""
        return "ready"


# -- module-level default engine (driver surface) -----------------------
_DEFAULT = None
_BACKEND_OVERRIDE = None


def _default_engine():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = KokoroSpeaker(backend=_BACKEND_OVERRIDE)
    return _DEFAULT


def engine_available() -> bool:
    """True when the Kokoro engine module imports in the CHILD
    interpreter (probed by a throwaway subprocess — this process
    never imports kokoro-onnx). The assets may still be missing
    — see engine_status()."""
    from . import kokoro_isolate as _isolate

    try:
        return bool(_isolate.probe_engine()["available"])
    except Exception:  # noqa: BLE001 — a probe fault is "unavailable"
        return False


def engine_status() -> dict:
    """Honest engine report: importability, the assets that would
    actually run, licence position. Never builds an ONNX session
    (asset presence + package importability are read, not run),
    and never imports kokoro-onnx in this process (the probe is
    a throwaway child interpreter)."""
    from . import kokoro_isolate as _isolate

    try:
        probe = _isolate.probe_engine()
        eng = _load_tree()
    except _EngineUnavailable as e:
        return {
            "ok": False,
            "available": False,
            "backend": None,
            "kokoro_available": False,
            "model_path": None,
            "voices_path": None,
            "model_ready": False,
            "degraded": True,
            "reason": str(e),
            "licence": None,
            "phonemizer_note": None,
        }
    if not probe["available"]:
        return {
            "ok": False,
            "available": False,
            "backend": None,
            "kokoro_available": False,
            "model_path": None,
            "voices_path": None,
            "model_ready": False,
            "degraded": True,
            "reason": probe["reason"],
            "licence": None,
            "phonemizer_note": None,
        }
    model_path, voices_path = eng["assets"].kokoro_paths()
    ready = bool(eng["assets"].kokoro_ready())
    return {
        "ok": True,
        "available": True,
        "backend": "kokoro-onnx" if ready else "kokoro-onnx (no assets)",
        "kokoro_available": ready,
        "model_path": str(model_path),
        "voices_path": str(voices_path),
        "model_ready": ready,
        "degraded": not ready,
        "reason": (None if ready else
                   "Kokoro assets not on disk (model + voices) — "
                   "the engine cannot speak until they are downloaded"),
        "licence": "MIT (kokoro-onnx wrapper); Apache-2.0 (Kokoro-82M model)",
        "phonemizer_note": (
            "phonemizer 3.4.0 is GPL-3.0 and runs ONLY in the "
            "isolated Kokoro child process (B-R1-3r, "
            "core/desktop/kokoro_isolate.py + kokoro_child.py) — "
            "never in the main process; espeak-ng is GPL-3.0 "
            "(row 120). See THIRD_PARTY_LICENSES.md."),
    }


def voice_status() -> dict:
    """Which voices are present and verified, which curated voices
    are missing. Reads the voices bin alone — never builds an
    ONNX session."""
    try:
        eng = _load_tree()
    except _EngineUnavailable as e:
        return {
            "ok": False,
            "available": False,
            "voices_present": None,
            "curated": None,
            "missing": None,
            "reason": str(e),
        }
    curated = {entry.code: entry.voice for entry in eng["languages"]}
    try:
        backend = _RealBackend()
        present = backend.voices()
    except Exception as e:  # noqa: BLE001
        present = None
    if present is None:
        return {
            "ok": True,
            "available": False,
            "voices_present": None,
            "curated": curated,
            "missing": sorted(curated.values()),
            "reason": "the voices bin could not be read",
        }
    missing = sorted(v for v in curated.values() if v not in present)
    return {
        "ok": True,
        "available": len(missing) == 0,
        "voices_present": list(present),
        "curated": curated,
        "missing": missing,
        "reason": (None if not missing else
                   f"{len(missing)} curated voice(s) missing from the bin"),
    }


def fallback_info() -> dict:
    """The Piper Amy fallback, named explicitly with its
    availability and licence flag. Never invokes Piper."""
    try:
        eng = _load_tree()
    except _EngineUnavailable:
        eng = None
    return _default_engine()._fallback(eng)


def set_backend(backend):
    """Test seam (clipboard idiom): replace the backend the
    default engine uses. Resets the default engine so the next
    call builds a fresh one on the new backend."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = backend
    _DEFAULT = None
    return {"ok": True}


def reset_backend():
    """Undo set_backend(): the default engine builds the real
    engine again on next use."""
    global _DEFAULT, _BACKEND_OVERRIDE
    _BACKEND_OVERRIDE = None
    _DEFAULT = None
    return {"ok": True}


def speak(text, *, language=None, voice=None, speed: float = 1.0,
          auto_switch: bool = True,
          timestamp: float | None = None) -> dict:
    # Loophole batch 5 (L-25): defence in depth — the SIP `say`
    # and dub paths reach TTS through this module function, not
    # through LocalLiveSessionSpeechMixin._speak, so the guard
    # runs here too. Same rule: tainted content is never spoken
    # verbatim or as the assistant's own words. The guard never
    # raises; a broken guard leaves the text unchanged.
    try:
        from ..speech_guard import guard_spoken_text

        text = guard_spoken_text(text)
    except Exception:  # noqa: BLE001 — the guard never breaks TTS
        pass
    return _default_engine().speak(
        text, language=language, voice=voice, speed=speed,
        auto_switch=auto_switch, timestamp=timestamp)


def reset() -> dict:
    return _default_engine().reset()
