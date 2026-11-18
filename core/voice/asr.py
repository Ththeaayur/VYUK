"""Local ASR path (v5.58, Wave 3, PR-10): faster-whisper with word timestamps.

This is the NEW canonical local-ASR module. It does NOT replace
core/voice/local_client.py's in-session ``_transcribe`` (that path is owned
elsewhere and still returns plain text); it is the word-timestamp
path the turn-behaviour and caption workstreams build on, and the
place a future integration wires ``_transcribe`` through.

Facts, not claims:
- faster-whisper is ALREADY a declared dependency (requirements.txt:
  ``faster-whisper>=1.0``) running on CTranslate2, not torch. This
  module verifies nothing new about the declaration beyond reading
  it; it adds no dependency.
- ``transcribe_with_words`` passes ``word_timestamps=True`` to
  faster-whisper's ``transcribe()`` explicitly, so every segment
  carries per-word ``start``/``end`` (and probability when the model
  reports one). A caller that needs only text uses
  ``transcribe_text``.
- When faster-whisper is not installed, or the model cannot load,
  the result reports ``engine="unavailable"`` with an ``error``
  string and empty text/segments. It NEVER synthesises a transcript
  and NEVER raises for a missing optional dependency. Malformed PCM
  (not bytes, odd length) is a caller bug and raises ValueError.
- Model resolution goes through ``core.voice.local_assets``'
  ``whisper_model_arg()`` when that module imports (bundled
  snapshot, hub snapshot, or effective-tier name — the same
  resolution the session path uses), falling back to the explicit
  ``model`` argument or ``"small"``. No weight file is bundled,
  downloaded, or written by this module.

Timing: every result carries ``elapsed_s`` (wall time of the
transcribe call). The §10 first-audio bar (local <=900 ms p95) is
MEASURED from that field across runs by the caller — this module
asserts no latency figure.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

FASTER_WHISPER_BEAM_SIZE = 5
FASTER_WHISPER_NO_SPEECH_THRESHOLD = 0.6
FASTER_WHISPER_LOG_PROB_THRESHOLD = -1.0


def faster_whisper_available() -> bool:
    """True when the faster-whisper package imports. Never raises."""
    try:
        import faster_whisper  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


# GAPS-16 (flaw #8): the ONE cached faster-whisper model, keyed by
# model argument. Before this, WhisperModel was constructed in FOUR
# places in the voice loop — here (a fresh model per transcription
# call), the local session's _ensure_whisper, the lite session's
# _ensure_whisper (its own copy of the local one), and the asset
# pack's download probe (which stays a separate construction by
# design: it builds then deletes a CPU model only to force the
# weights download at install time — see core/voice/
# local_assets_whisper.py). Two further constructions live outside
# the voice loop with their own contracts and are untouched:
# skills_senses_checks (best-effort clip, never downloads) and
# audio_localise (fixed model, test seam). The three live voice
# paths now share this factory: same weights, same
# device/compute_type, one load per model argument per process.
#
# The cache is STRONG on purpose, with one deliberate release:
# the local session's idle unload (core/voice/
# local_client_lifecycle.py _maybe_idle_unload) drops the model
# after effective_idle_unload_s() of STT inactivity to free RAM,
# and releases the factory entry with it — otherwise the unload
# would free nothing. A caller holding a model reference keeps
# its instance alive (the release only drops the FACTORY entry,
# by identity, so an in-flight transcription is never affected);
# the next load after a release simply constructs again, exactly
# as the old per-session code did. A failed construction is NOT
# cached: the next call retries, as before.
_SHARED_MODELS: dict[str, Any] = {}


# Field-check fix #18 (M10c): the first-turn hub download is bounded.
#
# The field check watched a first turn stall 6+ minutes inside the
# hub download of the `small` weights — the model construction has
# no timeout, no size cap and shows no progress, so the turn never
# completed. The bound has two halves, both enforced here in the
# one shared factory (every live turn funnels through it):
#
# 1. TIMEOUT on the wait, not on the download. A construction that
#    may download (a bare model name — no snapshot on disk) runs in
#    a daemon thread with a total deadline (WHISPER_LOAD_TIMEOUT_S).
#    On timeout the CALLER is unblocked and degrades to typed input;
#    the thread keeps going, so a download that later completes
#    still lands in the hub cache and the next call finds the
#    weights on disk and constructs from the local path. The
#    deadline is 180 s: the field check's own successful `small`
#    download took ~3 minutes, so a legitimate first fetch on a
#    normal connection fits inside it, while a stalled one detaches
#    at 3 minutes instead of hanging the turn forever. A local
#    snapshot path loads from disk and never touches the network,
#    so it constructs directly, with no thread and no deadline.
# 2. SIZE CAP on the landed weights. The measured `small` model.bin
#    is 483,546,902 bytes; its cap is 1 GB (~2x). The turbo tier is
#    capped at 2.5 GB, anything else at 4 GB. The cap is checked
#    BEFORE a construction (bytes already on disk are judged by a
#    cheap stat, never loaded) and AFTER one (the construction may
#    just have downloaded them). An over-cap hub-cache model.bin is
#    deleted — wrong bytes can never heal, so the next call
#    re-fetches — while a bundled snapshot is refused but never
#    deleted (install content). The SHA pin the provisioning path
#    enforces stays the integrity gate; this cap is the size gate.
#
# The construction itself stays the ONE site in this module (the
# test suite pins the count): _construct_whisper_model below.
# The provisioning fetch (core/voice/local_assets_whisper.py) keeps
# its own construction site and borrows _call_with_timeout for the
# same deadline — it must not hang setup forever either.
WHISPER_LOAD_TIMEOUT_S = 180

WHISPER_MODEL_MAX_BYTES = {
    "small": 1_000_000_000,
    "large-v3-turbo": 2_500_000_000,
}
WHISPER_MODEL_MAX_BYTES_DEFAULT = 4_000_000_000


def _human_bytes(num_bytes: int) -> str:
    """A short size for a log line ("483 MB", "2.5 GB")."""
    if num_bytes >= 1_000_000_000:
        return f"{num_bytes / 1_000_000_000:.1f} GB"
    if num_bytes >= 1_000_000:
        return f"{num_bytes / 1_000_000:.0f} MB"
    if num_bytes >= 1000:
        return f"{num_bytes / 1000:.0f} KB"
    return f"{num_bytes} B"


def _call_with_timeout(fn, timeout_s: float):
    """Run ``fn()`` in a daemon thread, waiting at most ``timeout_s``
    seconds. Returns ``(value, error, timed_out)``: ``value`` is
    ``fn()``'s return (None when it raised or timed out), ``error``
    is the exception it raised (None when it returned or timed
    out), ``timed_out`` is True when the deadline hit first. A
    timed-out call keeps running in its daemon thread — the caller
    is unblocked now, and a download that later completes still
    lands in the hub cache for the next call. Never raises."""
    cell: dict[str, Any] = {}

    def _run() -> None:
        try:
            cell["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            cell["error"] = exc

    worker = threading.Thread(target=_run, daemon=True,
                              name="whisper-load")
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return None, None, True
    if "error" in cell:
        return None, cell["error"], False
    return cell.get("value"), None, False


def _construct_whisper_model(model_arg: str) -> Any:
    """The ONE model construction in this module: builds the
    faster-whisper model for ``model_arg`` (``device="auto"``,
    ``compute_type="int8"`` — the construction every live path used
    before). A local snapshot path loads from disk; a bare name
    downloads from the hub. Raises when the package is missing or
    the load fails; the caller turns that into the degraded-input
    path."""
    from faster_whisper import WhisperModel
    return WhisperModel(model_arg, device="auto", compute_type="int8")


def _whisper_size_cap(model_arg: str) -> int:
    """The largest model.bin ``model_arg`` may carry: the cap for
    its model name (the effective model when the argument is a
    snapshot path), else the default cap. A bare repo id falls
    through to the effective name the same way."""
    name = model_arg
    if os.path.isabs(model_arg):
        try:
            from . import local_assets
            name = local_assets.effective_whisper_model_name()
        except Exception:  # noqa: BLE001
            name = ""
    return WHISPER_MODEL_MAX_BYTES.get(
        name or "", WHISPER_MODEL_MAX_BYTES_DEFAULT)


def _check_whisper_snapshot_size(model_arg: str) -> str | None:
    """The size verdict for the snapshot ``model_arg`` resolves to:
    None when it is within its cap (or there is no snapshot or no
    model.bin to judge), else the plain refusal sentence. A
    hub-cache model.bin over the cap is DELETED (wrong bytes can
    never heal — the next call re-fetches); a bundled snapshot is
    refused but never deleted. Never raises."""
    try:
        if os.path.isabs(model_arg):
            snapshot: Path | None = Path(model_arg)
            name = ""
            try:
                from . import local_assets
                name = local_assets.effective_whisper_model_name()
            except Exception:  # noqa: BLE001
                name = ""
        else:
            from . import local_assets
            name = model_arg
            snapshot = local_assets.whisper_snapshot_path(model_arg)
        if snapshot is None:
            return None
        model_bin = Path(snapshot) / "model.bin"
        if not model_bin.is_file():
            return None
        size = model_bin.stat().st_size
        cap = WHISPER_MODEL_MAX_BYTES.get(
            name or "", WHISPER_MODEL_MAX_BYTES_DEFAULT)
        if size <= cap:
            return None
        try:
            from . import local_assets as _la
            bundled = _la.bundled_whisper_path(name or None)
        except Exception:  # noqa: BLE001
            bundled = None
        if bundled is not None:
            try:
                is_bundled = Path(snapshot).resolve() == Path(bundled).resolve()
            except Exception:  # noqa: BLE001
                is_bundled = False
        else:
            is_bundled = False
        if is_bundled:
            return (
                f"Whisper model for {name or model_arg} is "
                f"{_human_bytes(size)} — over the {_human_bytes(cap)} "
                "cap; refusing the bundled copy (install content, so "
                "it was left in place — voice input will retry when "
                "you connect; typing works meanwhile.)"
            )
        try:
            model_bin.unlink()
        except OSError:
            pass
        return (
            f"Whisper model for {name or model_arg} is "
            f"{_human_bytes(size)} — over the {_human_bytes(cap)} cap; "
            "the oversized file was removed, so it will re-download "
            "on the next attempt."
        )
    except Exception:  # noqa: BLE001 — an unreadable snapshot is not a verdict
        return None


def _load_whisper_model(model_arg: str):
    """The bounded load for ``model_arg``. Returns ``(model, kind,
    note)``: kind is "ok" (model is the loaded model, note None),
    "timeout", "oversize" or "error" (model None, note the plain
    sentence for the log). Never raises. The size gate runs before
    the construction (bytes on disk are judged, never loaded) and
    after it (the construction may just have downloaded them)."""
    size_err = _check_whisper_snapshot_size(model_arg)
    if size_err is not None:
        return None, "oversize", size_err
    if os.path.isabs(model_arg):
        # A local snapshot path loads from disk — no network, no
        # wait to bound — so it constructs directly.
        try:
            return _construct_whisper_model(model_arg), "ok", None
        except Exception as exc:  # noqa: BLE001
            return None, "error", f"Whisper model failed to load: {exc}"
    value, error, timed_out = _call_with_timeout(
        lambda: _construct_whisper_model(model_arg),
        WHISPER_LOAD_TIMEOUT_S)
    if timed_out:
        return None, "timeout", (
            f"Whisper model load timed out after "
            f"{WHISPER_LOAD_TIMEOUT_S}s ({model_arg}) — voice input "
            "will retry when you connect; typing works meanwhile.")
    if error is not None:
        return None, "error", f"Whisper model failed to load: {error}"
    size_err = _check_whisper_snapshot_size(model_arg)
    if size_err is not None:
        return None, "oversize", size_err
    return value, "ok", None


def get_whisper_model(model: str | None = None) -> Any | None:
    """The shared faster-whisper model for ``model`` (the effective
    model when none is named — see resolve_model_arg). Returns the
    cached instance when one is alive, else constructs it once
    (``device="auto"``, ``compute_type="int8"`` — the construction
    every live path used before). Returns None when
    faster-whisper is not installed or the load fails (the caller
    degrades to typed input, as before); the failure is printed
    once per attempt, as the old _ensure_whisper paths did.

    Field-check fix #18 (M10c): a construction that may download
    (a bare model name — no snapshot on disk) is bounded by
    _load_whisper_model: a total wait deadline plus a size cap on
    the landed weights. A timed-out or oversized load returns None
    exactly like any other load failure."""
    model_arg = resolve_model_arg(model)
    cached = _SHARED_MODELS.get(model_arg)
    if cached is not None:
        return cached
    if not faster_whisper_available():
        return None
    model_obj, kind, note = _load_whisper_model(model_arg)
    if kind != "ok":
        if note:
            print(f"[STT] {note}")
        return None
    _SHARED_MODELS[model_arg] = model_obj
    return model_obj


def release_whisper_model(model: Any) -> None:
    """Drop the factory entry holding ``model`` (identity-checked):
    the idle unload's release. Callers holding their own reference
    keep using their instance; the next factory call after a
    release constructs again. A model that was never cached, or an
    entry already replaced by a newer instance, is left alone."""
    for key, cached in list(_SHARED_MODELS.items()):
        if cached is model:
            del _SHARED_MODELS[key]


def resolve_model_arg(model: str | None = None) -> str:
    """The model argument faster-whisper receives.

    Explicit ``model`` wins; otherwise ``local_assets.
    whisper_model_arg()`` (bundled/hub/tier resolution); otherwise
    ``"small"``. Never raises — resolution failure degrades to
    ``"small"`` and the load error surfaces in the result dict.
    """
    if model:
        return str(model)
    try:
        from . import local_assets  # Phase 3 V2: local_assets now in core/voice/; V1a-1 interim closed
        return str(local_assets.whisper_model_arg())
    except Exception:  # noqa: BLE001
        return "small"


def _pcm_to_float(pcm_bytes: bytes) -> Any:
    import numpy as np

    if not isinstance(pcm_bytes, (bytes, bytearray)):
        raise ValueError("pcm_bytes must be bytes of int16 PCM")
    raw = bytes(pcm_bytes)
    if len(raw) % 2 != 0:
        raise ValueError("pcm_bytes must hold whole int16 samples")
    if len(raw) == 0:
        raise ValueError("pcm_bytes is empty")
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _segment_to_dict(segment: Any) -> dict[str, Any]:
    words = []
    for word in (getattr(segment, "words", None) or ()):
        try:
            words.append({
                "word": str(getattr(word, "word", "")),
                "start": float(getattr(word, "start", 0.0)),
                "end": float(getattr(word, "end", 0.0)),
                "probability": (
                    float(getattr(word, "probability"))
                    if getattr(word, "probability", None) is not None
                    else None
                ),
            })
        except Exception:  # noqa: BLE001
            continue
    return {
        "text": str(getattr(segment, "text", "")),
        "start": float(getattr(segment, "start", 0.0)),
        "end": float(getattr(segment, "end", 0.0)),
        "words": words,
    }


def transcribe_with_words(
    pcm_bytes: bytes,
    *,
    sample_rate: int = 16000,
    model: str | None = None,
    language: str | None = None,
    beam_size: int = FASTER_WHISPER_BEAM_SIZE,
    word_timestamps: bool = True,
    initial_prompt: str | None = None,
    no_speech_threshold: float | None = None,
    log_prob_threshold: float | None = None,
) -> dict[str, Any]:
    """Transcribe 16 kHz int16 mono PCM with word timestamps.

    Returns a dict with keys: ``engine`` (``"faster-whisper"`` or
    ``"unavailable"``), ``text``, ``segments`` (each with ``words``),
    ``language`` (detected, when reported), ``model_arg``,
    ``elapsed_s``, and ``error`` (None on success). ``sample_rate``
    is recorded for the caller; faster-whisper consumes float audio
    and the 16 kHz mic path is the rate this tree records at, so a
    non-16 kHz input is reported as an error rather than silently
    resampled into a wrong timescale.

    Voice-control batch (2026-11-16, V5): this is the ONE shared
    STT call — the live sessions' ``_transcribe`` methods call it
    through :func:`transcribe_text` with
    ``_transcription_settings()`` as the single kwargs source, so
    the locked language, beam and biasing prompt apply to live
    turns exactly as they do to meetings. ``initial_prompt`` /
    thresholds default to the module constants when not given.
    """
    started = time.monotonic()
    model_arg = resolve_model_arg(model)

    def _unavailable(error: str) -> dict[str, Any]:
        return {
            "engine": "unavailable",
            "text": "",
            "segments": [],
            "language": language,
            "model_arg": model_arg,
            "elapsed_s": time.monotonic() - started,
            "error": error,
        }

    if int(sample_rate) != 16000:
        return _unavailable(
            f"unsupported sample_rate {sample_rate!r}: this path "
            "takes 16 kHz mic PCM only (no silent resampling)")
    try:
        audio = _pcm_to_float(pcm_bytes)
    except ValueError:
        raise
    if not faster_whisper_available():
        return _unavailable(
            "faster-whisper is not installed — voice input disabled "
            "(typed input still works)")
    whisper = get_whisper_model(model)
    if whisper is None:
        return _unavailable(
            "faster-whisper model failed to load — voice input "
            "disabled (typed input still works)")
    try:
        segments, info = whisper.transcribe(
            audio,
            language=language,
            beam_size=int(beam_size),
            word_timestamps=bool(word_timestamps),
            initial_prompt=initial_prompt,
            no_speech_threshold=(
                FASTER_WHISPER_NO_SPEECH_THRESHOLD
                if no_speech_threshold is None
                else float(no_speech_threshold)),
            log_prob_threshold=(
                FASTER_WHISPER_LOG_PROB_THRESHOLD
                if log_prob_threshold is None
                else float(log_prob_threshold)),
        )
        seg_dicts = [_segment_to_dict(s) for s in segments]
        text = " ".join(s["text"] for s in seg_dicts).strip()
        return {
            "engine": "faster-whisper",
            "text": text,
            "segments": seg_dicts,
            "language": getattr(info, "language", language),
            "model_arg": model_arg,
            "elapsed_s": time.monotonic() - started,
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return _unavailable(f"faster-whisper transcription failed: {e}")


def transcribe_text(pcm_bytes: bytes, **kwargs: Any) -> str:
    """Plain-text convenience over :func:`transcribe_with_words`."""
    return str(transcribe_with_words(pcm_bytes, **kwargs).get("text", ""))
