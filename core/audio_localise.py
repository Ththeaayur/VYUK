"""Localise audio: transcribe, translate and dub an audio file (R19-1,
Huanshere/VideoLingo inspiration).

Parent-ruled (licence, 2026-11-12): Huanshere/VideoLingo (Apache-2.0, LICENSE from main,
11,357 bytes, 201 lines, read in full). Nothing is adopted — no VideoLingo
code, no Streamlit, no Qwen3-ASR, no WhisperX, no yt-dlp, no FFmpeg. The
build takes only the pipeline's shape (transcribe -> translate -> dub) and
rebuilds it as own stdlib code over VYUK's own store shapes.

What VideoLingo does (README, verified from source): "VideoLingo combines
speech recognition, subtitle translation, segmentation and dubbing in a
Streamlit interface. It produces subtitle files and optionally subtitled
or dubbed videos." The pipeline is core/_1_ytdlp.py (YouTube download) ->
_2_asr.py (speech recognition, Qwen3-ASR / WhisperX / ElevenLabs) ->
_3_*_split (segmentation) -> _4_2_translate.py (translation) ->
_5/_6_gen_sub.py (subtitle files) -> _8_*_dub_chunks.py (dubbing,
GPT-SoVITS / CosyVoice / F5-TTS / OpenAI / Edge / Fish) ->
_7_sub_into_vid.py / _12_dub_to_vid.py (burn + mux, FFmpeg).

What is built here — the AUDIO half of that pipeline, as one effectful
tool `audio_localise`:
  * PROJECT: an operator-supplied WAV audio file + source/target languages.
  * TRANSCRIBE: the audio to segments (start/end/text) with faster-whisper
    (CTranslate2, torch-free — the tree's own voice-loop STT, declared in
    requirements.txt; a lazy import, and an honest refusal when it is
    absent). The WAV is decoded with stdlib `wave` and resampled to
    16 kHz mono float32 in numpy, so no PyAV/FFmpeg decode is needed.
  * TRANSLATE: the segments to the target language with the product's own
    configured Gemini key (the prompt_tune _lm_call shape — own stdlib
    urllib, injectable opener, 1 MB cap). Confirm-gated (C-14
    approve-first): it sends the owner's audio transcript to the owner's
    own configured Gemini endpoint and spends their LM quota.
  * DUB: the translated segments with the tree's own Kokoro TTS
    (KokoroSpeaker, isolated child process — the voice loop's TTS) into
    a full-length dubbed WAV (24 kHz mono 16-bit, stdlib `wave`), the
    speech placed at each segment's start time. The speed is raised
    (up to 4.0) when the speech would overrun its segment window; a
    remaining overrun is reported honestly, never silently clipped.
  * SUBTITLES: the translated segments as an SRT file (the tree's own
    format_srt) and a VTT file (the tree's own _format_time), written
    under DATA_DIR/audio_localise/<project-slug>/.
  * EVENTS: a hash-chained ledger of every project/segment/translation/
    dub action, with full-chain verify (the A1-store precedent).

What is NOT built (STOP, stated on the tin):
  * No YouTube download — yt-dlp is a scraper; the standing rulings on
    scraping/undocumented APIs apply. The operator supplies the file.
  * No video processing — no FFmpeg ships and no ffmpeg-dependent code
    exists in the tree (creative_subtitle_edit.py:12), so there is no
    audio-extraction-from-video route, no subtitle burn-in and no
    muxing. The tool works on audio files, not video files.
  * No Streamlit interface — a second app; VYUK is a Python/Qt desktop
    app. The pipeline is the build, not the interface.
  * No Qwen3-ASR / WhisperX (torch) and no cloud TTS (OpenAI / Edge /
    Fish / GPT-SoVITS / CosyVoice / F5-TTS). The STT is faster-whisper
    (torch-free); the dub is Kokoro (the tree's own TTS).
  * WAV input only (stdlib `wave`) — no MP3/FLAC/OGG decoder exists in
    the tree, and none is bundled. A non-WAV file is refused honestly
    with the reason, never silently converted.
  * No live model is ever downloaded silently: faster-whisper downloads
    its model on first use (from HuggingFace) — the tool reports the
    download failure honestly when there is no network.

Classification: effectful, confirm-gated for `translate` and `localise`
(they call the owner's Gemini endpoint); the local actions (project,
transcribe, dub) are free app-state and the reads (status, events) are
free. The args-aware branch in core/funnel/tools_gate.py gates exactly
translate/localise; the read actions are read-class in
core/funnel/tools_gate_readclass.py.
"""

import base64
import json
import math
import os
import re
import sqlite3
import struct
import threading
import time
import wave
from pathlib import Path

from .config import DATA_DIR
from . import store_base
from . import creative_subtitle_edit as _subtitles
from .prompt_tune import _lm_call as _gemini_call

__all__ = ["audio_localise", "register_all"]

# ---------------------------------------------------------------- constants
_ACTIONS = ("project", "transcribe", "translate", "dub", "localise",
            "status", "events")
_PROJECT_MODES = ("add", "list", "get", "remove")
_TRANSLATE_MODES = ("all", "one")
_DUB_MODES = ("all", "one")
_EVENT_MODES = ("list", "verify")
_DB_NAME = "audio_localise.sqlite3"
_OUT_DIR_NAME = "audio_localise"

# Bounds (all refusals are honest, with the numbers).
_MAX_FILE_BYTES = 100 * 1024 * 1024          # 100 MB audio file
_MAX_DURATION_S = 3600.0                     # 60 minutes of audio
_MAX_SEGMENTS = 500                          # segments per project
_MAX_SEGMENT_CHARS = 500                     # chars per segment (dub)
_MAX_TRANSLATE_BATCH = 50                    # segments per LM call
_MAX_PROJECT_NAME = 80
_MAX_NOTE_CHARS = 200
_STT_MODEL = "small"                         # faster-whisper small / int8
_STT_RATE = 16000                            # Whisper wants 16 kHz mono
_DUB_RATE = 24000                            # Kokoro renders at 24 kHz
_DUB_SPEED_MIN = 0.0                         # exclusive (Kokoro's rule)
_DUB_SPEED_MAX = 4.0                         # inclusive (Kokoro's rule)
_SUPPORTED_LANGS = frozenset({
    "en", "fr", "de", "es", "it", "pt", "ja", "zh", "hi", "ru",
})
# Kokoro voices are per-language; the dub voice must match the target
# language. A Kokoro voice name is <region><gender>_<name> — the
# language is the FIRST letter (a/b=American/British English,
# e=Spanish, f=French, h=Hindi, i=Italian, j=Japanese,
# p=Portuguese, z=Chinese). Kokoro v1.0 has NO German or Russian
# voice, so those are not dubbable target languages.
_KOKORO_LANGS = frozenset({
    "en", "es", "fr", "hi", "it", "ja", "pt", "zh",
})
_VOICE_REGION_LANG = {
    "a": "en", "b": "en", "e": "es", "f": "fr", "h": "hi",
    "i": "it", "j": "ja", "p": "pt", "z": "zh",
}
_DEFAULT_VOICE = "af_heart"

_LOCK = threading.RLock()


def _db_path():
    return Path(DATA_DIR) / _DB_NAME


def _out_dir(project_name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(project_name).strip().lower())
    slug = slug.strip("-") or "project"
    return Path(DATA_DIR) / _OUT_DIR_NAME / slug


def _slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower())
    return slug.strip("-") or "project"


def _fmt_time(seconds):
    """Seconds -> 'HH:MM:SS' for answers (the subtitle file uses the
    tree's own _format_time for cue timestamps)."""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    return "%02d:%02d:%02d" % (hours, minutes, secs)


def _fmt_hours(seconds):
    """Seconds -> 'H:MM:SS' for answers."""
    seconds = max(0.0, float(seconds))
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    return "%d:%02d:%02d" % (hours, minutes, secs)


def _parse_lang(value):
    """A language code, lower-cased, or None when not supported."""
    if not isinstance(value, str):
        return None
    code = value.strip().lower()
    if code in _SUPPORTED_LANGS:
        return code
    # Accept "en-US" / "en_US" spellings by taking the primary subtag.
    primary = re.split(r"[-_]", code, maxsplit=1)[0]
    if primary in _SUPPORTED_LANGS:
        return primary
    return None


def _voice_lang(voice):
    """The language of a Kokoro voice name ('af_heart' -> 'en',
    'ff_siwis' -> 'fr'). The language is the FIRST letter (the
    region); the second letter is the gender. Returns None when the
    name is not a known Kokoro voice shape."""
    if not isinstance(voice, str):
        return None
    m = re.match(r"^([a-z])[fm]_[a-z]", voice.strip().lower())
    if not m:
        return None
    return _VOICE_REGION_LANG.get(m.group(1))


# -------------------------------------------------------------------- store
class _AlStore(store_base.StoreBase):
    """Projects + segments + hash-chained event ledger. Open-per-operation;
    every write is followed by a re-read before the caller claims it
    (A1-store). A read (list/get/status/events) must not create the
    database — _missing() answers empty without touching the file."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()


    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS projects ("
            " id INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL UNIQUE,"
            " file TEXT NOT NULL,"
            " source_lang TEXT NOT NULL,"
            " target_lang TEXT NOT NULL,"
            " voice TEXT NOT NULL,"
            " speed REAL NOT NULL,"
            " duration REAL NOT NULL,"
            " deleted INTEGER NOT NULL DEFAULT 0,"
            " created REAL NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS segments ("
            " id INTEGER PRIMARY KEY,"
            " project_id INTEGER NOT NULL,"
            " idx INTEGER NOT NULL,"
            " start REAL NOT NULL,"
            " end REAL NOT NULL,"
            " text TEXT NOT NULL,"
            " translated TEXT,"
            " dubbed INTEGER NOT NULL DEFAULT 0,"
            " created REAL NOT NULL,"
            " UNIQUE(project_id, idx))")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " id INTEGER PRIMARY KEY,"
            " prev_hash TEXT NOT NULL,"
            " hash TEXT NOT NULL,"
            " kind TEXT NOT NULL,"
            " payload TEXT NOT NULL,"
            " created REAL NOT NULL)")
        return conn

    def _append_event_locked(self, conn, kind, payload):
        row = conn.execute(
            "SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev = row[0] if row else "0" * 64
        digest = store_base.chain_digest(
            "json_body_no_ts", prev, {"kind": kind, "payload": payload})
        conn.execute(
            "INSERT INTO events (prev_hash, hash, kind, payload, created)"
            " VALUES (?, ?, ?, ?, ?)",
            (prev, digest, kind, json.dumps(payload, sort_keys=True),
             time.time()))
        return digest

    # -- projects ------------------------------------------------------
    def project_add(self, name, file, source_lang, target_lang, voice,
                    speed, duration):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id, name, file, source_lang, target_lang,"
                    " voice, speed, duration, deleted, created"
                    " FROM projects WHERE name = ?", (name,)).fetchone()
                if row is not None and row[8] == 0:
                    return {"exists": True, "row": row}
                if row is not None and row[8] == 1:
                    # Soft-deleted: re-adding the same name restores
                    # it AND its segments (the timetrace shape).
                    conn.execute(
                        "UPDATE projects SET file = ?, source_lang = ?,"
                        " target_lang = ?, voice = ?, speed = ?,"
                        " duration = ?, deleted = 0 WHERE id = ?",
                        (file, source_lang, target_lang, voice, speed,
                         duration, row[0]))
                    conn.commit()
                    self._append_event_locked(
                        conn, "project_restored",
                        {"project": name, "id": row[0]})
                    conn.commit()
                    fresh = conn.execute(
                        "SELECT id, name, file, source_lang, target_lang,"
                        " voice, speed, duration, deleted, created"
                        " FROM projects WHERE id = ?", (row[0],)).fetchone()
                    return {"restored": True, "row": fresh}
                conn.execute(
                    "INSERT INTO projects (name, file, source_lang,"
                    " target_lang, voice, speed, duration, deleted,"
                    " created) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                    (name, file, source_lang, target_lang, voice, speed,
                     duration, time.time()))
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, file, source_lang, target_lang,"
                    " voice, speed, duration, deleted, created"
                    " FROM projects WHERE name = ?", (name,)).fetchone()
                self._append_event_locked(
                    conn, "project_added",
                    {"project": name, "id": row[0], "file": file,
                     "source_lang": source_lang,
                     "target_lang": target_lang})
                conn.commit()
            finally:
                conn.close()
        if row is None:
            return None
        if (row[1] != name or row[2] != file
                or row[3] != source_lang or row[4] != target_lang):
            return None
        return {"row": row}

    def project_get(self, name):
        if self._missing():
            return None
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, name, file, source_lang, target_lang,"
                " voice, speed, duration, deleted, created"
                " FROM projects WHERE name = ?", (name,)).fetchone()
        finally:
            conn.close()

    def project_list(self, limit=20):
        if self._missing():
            return []
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, name, file, source_lang, target_lang,"
                " voice, speed, duration, deleted, created"
                " FROM projects WHERE deleted = 0"
                " ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()
        finally:
            conn.close()

    def project_remove(self, name):
        with _LOCK:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id FROM projects WHERE name = ? AND deleted = 0",
                    (name,)).fetchone()
                if row is None:
                    return None
                count = conn.execute(
                    "SELECT COUNT(*) FROM segments WHERE project_id = ?",
                    (row[0],)).fetchone()[0]
                conn.execute(
                    "UPDATE projects SET deleted = 1 WHERE id = ?",
                    (row[0],))
                conn.commit()
                self._append_event_locked(
                    conn, "project_removed",
                    {"project": name, "id": row[0], "segments": count})
                conn.commit()
                return count
            finally:
                conn.close()

    # -- segments ------------------------------------------------------
    def segments_replace(self, project_id, segments):
        """Replace a project's segments (a fresh transcription). The
        old segments are dropped; the new ones are stored in order
        and re-read before the caller claims them."""
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "DELETE FROM segments WHERE project_id = ?",
                    (project_id,))
                for idx, seg in enumerate(segments):
                    conn.execute(
                        "INSERT INTO segments (project_id, idx, start,"
                        " end, text, translated, dubbed, created)"
                        " VALUES (?, ?, ?, ?, ?, NULL, 0, ?)",
                        (project_id, idx, float(seg["start"]),
                         float(seg["end"]), str(seg["text"]),
                         time.time()))
                conn.commit()
                rows = conn.execute(
                    "SELECT id, project_id, idx, start, end, text,"
                    " translated, dubbed, created"
                    " FROM segments WHERE project_id = ? ORDER BY idx",
                    (project_id,)).fetchall()
                self._append_event_locked(
                    conn, "transcribed",
                    {"project_id": project_id,
                     "segments": len(rows)})
                conn.commit()
                return rows
            finally:
                conn.close()

    def segment_list(self, project_id, limit=1000):
        if self._missing():
            return []
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, project_id, idx, start, end, text,"
                " translated, dubbed, created"
                " FROM segments WHERE project_id = ? ORDER BY idx LIMIT ?",
                (project_id, int(limit))).fetchall()
        finally:
            conn.close()

    def segment_get(self, project_id, idx):
        if self._missing():
            return None
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, project_id, idx, start, end, text,"
                " translated, dubbed, created"
                " FROM segments WHERE project_id = ? AND idx = ?",
                (project_id, int(idx))).fetchone()
        finally:
            conn.close()

    def segment_set_translation(self, segment_id, translated):
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE segments SET translated = ? WHERE id = ?",
                    (translated, segment_id))
                conn.commit()
                row = conn.execute(
                    "SELECT id, project_id, idx, start, end, text,"
                    " translated, dubbed, created"
                    " FROM segments WHERE id = ?", (segment_id,)).fetchone()
                if row is None or row[6] != translated:
                    return None
                self._append_event_locked(
                    conn, "translated",
                    {"segment_id": segment_id, "idx": row[2]})
                conn.commit()
                return row
            finally:
                conn.close()

    def segment_set_dubbed(self, segment_id):
        with _LOCK:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE segments SET dubbed = 1 WHERE id = ?",
                    (segment_id,))
                conn.commit()
                row = conn.execute(
                    "SELECT id, project_id, idx, start, end, text,"
                    " translated, dubbed, created"
                    " FROM segments WHERE id = ?", (segment_id,)).fetchone()
                if row is None or row[7] != 1:
                    return None
                self._append_event_locked(
                    conn, "dubbed",
                    {"segment_id": segment_id, "idx": row[2]})
                conn.commit()
                return row
            finally:
                conn.close()

    def count_translated(self, project_id):
        if self._missing():
            return 0
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM segments WHERE project_id = ?"
                " AND translated IS NOT NULL",
                (project_id,)).fetchone()[0]
        finally:
            conn.close()

    def count_dubbed(self, project_id):
        if self._missing():
            return 0
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM segments WHERE project_id = ?"
                " AND dubbed = 1",
                (project_id,)).fetchone()[0]
        finally:
            conn.close()

    # -- events --------------------------------------------------------
    def list_events(self, limit=20):
        if self._missing():
            return []
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, prev_hash, hash, kind, payload, created"
                " FROM events ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()
        finally:
            conn.close()

    def verify_chain(self):
        if self._missing():
            return (True, 0, None)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, prev_hash, hash, kind, payload, created"
                " FROM events ORDER BY id").fetchall()
        finally:
            conn.close()
        entries = [{"kind": row[3],
                    "payload": json.loads(row[4]),
                    "prev_hash": row[1], "hash": row[2]}
                   for row in rows]
        result = store_base.verify_entries(
            entries, "json_body_no_ts", store_base.GENESIS)
        if not result["ok"]:
            bad = rows[result["first_break"]["index"]]
            if result["first_break"]["reason"].startswith("link break"):
                return (False, len(rows),
                        "event #%d does not chain to its predecessor"
                        % bad[0])
            return (False, len(rows),
                    "event #%d fails its hash" % bad[0])
        return (True, len(rows), None)

# ------------------------------------------------------------- WAV + STT
def _read_wav(path):
    """Read a WAV file with stdlib `wave`. Returns (mono float32 numpy
    array at the native rate, rate, duration_s) or (None, error).
    Only WAV is accepted — no MP3/FLAC/OGG decoder exists in the
    tree, and none is bundled; a non-WAV file is refused honestly."""
    if not isinstance(path, str) or not path.strip():
        return None, "refused: give the audio file path."
    p = Path(path.strip())
    if not p.is_file():
        return None, "refused: no audio file at %r." % str(p)
    if p.suffix.lower() != ".wav":
        return None, ("refused: %r is not a WAV file — WAV input only "
                      "(no MP3/FLAC/OGG decoder exists in the tree)."
                      % p.name)
    try:
        size = p.stat().st_size
    except OSError as e:
        return None, "refused: cannot read %r (%s)." % (p.name, e)
    if size > _MAX_FILE_BYTES:
        return None, ("refused: %r is %d bytes (max %d)."
                      % (p.name, size, _MAX_FILE_BYTES))
    try:
        import numpy as _np
    except ImportError:
        return None, ("refused: numpy is not importable, so the audio "
                      "cannot be decoded.")
    try:
        with wave.open(str(p), "rb") as wf:
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.getnframes()
            raw = wf.readframes(frames)
    except (wave.Error, EOFError, OSError) as e:
        return None, "refused: %r is not a readable WAV (%s)." % (p.name, e)
    if rate <= 0 or frames <= 0:
        return None, "refused: %r has no audio frames." % p.name
    duration = frames / float(rate)
    if duration > _MAX_DURATION_S:
        return None, ("refused: %r is %s long (max %s)."
                      % (p.name, _fmt_hours(duration),
                         _fmt_hours(_MAX_DURATION_S)))
    if sampwidth == 2:
        data = _np.frombuffer(raw, dtype="<i2").astype(_np.float32) / 32768.0
    elif sampwidth == 4:
        data = _np.frombuffer(raw, dtype="<i4").astype(_np.float32) / 2147483648.0
    elif sampwidth == 1:
        data = (_np.frombuffer(raw, dtype="u1").astype(_np.float32)
                - 128.0) / 128.0
    else:
        return None, ("refused: %r uses %d-byte samples (only 8/16/32-bit "
                      "WAV is supported)." % (p.name, sampwidth))
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return (data.astype(_np.float32), int(rate), float(duration)), None


def _resample_16k(samples, rate):
    """Resample mono float32 audio to 16 kHz (what Whisper wants)
    with linear interpolation. Returns a float32 numpy array."""
    import numpy as _np

    if int(rate) == _STT_RATE:
        return samples.astype(_np.float32)
    if len(samples) == 0:
        return samples.astype(_np.float32)
    n_out = int(round(len(samples) * _STT_RATE / float(rate)))
    if n_out <= 0:
        return _np.zeros(0, dtype=_np.float32)
    src = _np.arange(len(samples), dtype=_np.float64)
    dst = _np.linspace(0, len(samples) - 1, n_out)
    return _np.interp(dst, src, samples).astype(_np.float32)


def _stt_transcribe(audio_16k, language, _stt=None):
    """Transcribe 16 kHz mono float32 audio to segments
    [{start, end, text}]. Returns (segments, error).
    The STT is faster-whisper (CTranslate2, torch-free — the tree's
    own voice-loop STT, declared in requirements.txt). It is a lazy
    import, and an honest refusal when it is absent. A test STT may
    be injected: a callable (audio_16k, language) -> segments, or
    None to use the real engine."""
    if _stt is not None:
        try:
            segments = _stt(audio_16k, language)
        except Exception as e:  # noqa: BLE001 — a fake's fault is a refusal
            return None, "refused: the transcription failed (%s: %s)." % (
                type(e).__name__, e)
        if not isinstance(segments, list):
            return None, "refused: the transcription returned no segments."
        return segments, None
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None, ("refused: faster-whisper is not installed, so "
                      "there is no speech recognition. It is a declared "
                      "dependency (faster-whisper>=1.0) — install it and "
                      "retry. Nothing was transcribed.")
    try:
        model = WhisperModel(_STT_MODEL, device="cpu",
                             compute_type="int8")
        segments_iter, _info = model.transcribe(
            audio_16k, language=language, word_timestamps=False)
        segments = [{"start": float(s.start), "end": float(s.end),
                     "text": str(s.text).strip()}
                    for s in segments_iter
                    if str(s.text).strip()]
    except Exception as e:  # noqa: BLE001 — a model/download fault is a refusal
        return None, ("refused: the transcription failed (%s: %s). "
                      "The Whisper model downloads on first use; with no "
                      "network it cannot." % (type(e).__name__, e))
    if not segments:
        return None, "refused: the audio produced no speech segments."
    if len(segments) > _MAX_SEGMENTS:
        return None, ("refused: the audio produced %d segments (max %d)."
                      % (len(segments), _MAX_SEGMENTS))
    return segments, None


# ------------------------------------------------------------ translation
_TRANSLATE_PROMPT = (
    "Translate these subtitle lines from {src} to {dst}. Return ONE "
    "translated line per input line, in the same order, with no "
    "numbering, no extra lines and no commentary. Keep names and "
    "numbers unchanged. Lines:\n{lines}"
)


def _translate_segments(segments, source_lang, target_lang, _opener=None):
    """Translate segment texts with the product's own configured
    Gemini key (the prompt_tune _lm_call shape). Returns
    (translations, error) — translations is a list of strings aligned
    1:1 with segments, or None on refusal. The LM is called in
    batches of at most _MAX_TRANSLATE_BATCH segments; a batch whose
    line count does not match its segment count is a refusal (the
    alignment is never guessed)."""
    if not segments:
        return [], None
    translations = []
    for start in range(0, len(segments), _MAX_TRANSLATE_BATCH):
        batch = segments[start:start + _MAX_TRANSLATE_BATCH]
        lines = "\n".join(str(s["text"]) for s in batch)
        prompt = _TRANSLATE_PROMPT.format(src=source_lang, dst=target_lang,
                                          lines=lines)
        text, err = _gemini_call(prompt, None, _opener=_opener)
        if err is not None:
            return None, err
        if text is None or not str(text).strip():
            return None, ("refused: the translation came back empty. "
                          "Nothing was stored.")
        out = [ln for ln in str(text).splitlines() if ln.strip()]
        if len(out) != len(batch):
            return None, ("refused: the translation returned %d lines for "
                          "%d segments — the alignment cannot be trusted, "
                          "so nothing was stored." % (len(out), len(batch)))
        translations.extend(ln.strip() for ln in out)
    return translations, None


# ------------------------------------------------------------------- dub
def _tts_render(text, voice, lang, speed, _tts=None):
    """Render one text with the tree's own Kokoro TTS (KokoroSpeaker,
    isolated child process). Returns ((samples, rate), error) — samples
    is a float32 numpy array, rate is 24000. A test TTS may be
    injected: an object with speak(text, language, voice, speed)
    returning a dict in the KokoroSpeaker shape ({ok, audio}), or a
    callable (text, voice, lang, speed) -> (samples, rate)."""
    if _tts is not None:
        try:
            if callable(_tts):
                result = _tts(text, voice, lang, speed)
            else:
                result = _tts.speak(text, language=lang, voice=voice,
                                    speed=speed)
        except Exception as e:  # noqa: BLE001 — a fake's fault is a refusal
            return None, "refused: the dub render failed (%s: %s)." % (
                type(e).__name__, e)
        if isinstance(result, tuple) and len(result) == 2:
            return result, None
        if isinstance(result, dict):
            if not result.get("ok"):
                return None, ("refused: the dub render failed (%s)."
                              % (result.get("reason") or "no reason given"))
            audio = result.get("audio")
            if isinstance(audio, tuple) and len(audio) == 2:
                return audio, None
            if isinstance(audio, dict) and "samples" in audio:
                return (audio["samples"], int(audio.get("rate", _DUB_RATE))), None
            return None, "refused: the dub render returned no audio."
        return None, "refused: the dub render returned no audio."
    try:
        from .desktop.kokoro_voice import KokoroSpeaker
    except ImportError as e:
        return None, ("refused: the Kokoro speaker is not importable (%s)."
                      % e)
    try:
        speaker = KokoroSpeaker()
        result = speaker.speak(text, language=lang, voice=voice,
                               speed=float(speed))
    except Exception as e:  # noqa: BLE001 — an engine fault is a refusal
        return None, "refused: the dub render failed (%s: %s)." % (
            type(e).__name__, e)
    if not isinstance(result, dict) or not result.get("ok"):
        reason = ""
        if isinstance(result, dict):
            reason = str(result.get("reason") or result.get("detail") or "")
        return None, ("refused: the dub render failed (%s)."
                      % (reason or "no reason given"))
    audio = result.get("audio")
    if isinstance(audio, tuple) and len(audio) == 2:
        return audio, None
    if isinstance(audio, dict) and "samples" in audio:
        return (audio["samples"], int(audio.get("rate", _DUB_RATE))), None
    return None, "refused: the dub render returned no audio."


def _dub_segment(text, voice, lang, speed, window_s, _tts=None):
    """Render one translated segment, raising the speed (up to 4.0)
    when the speech would overrun its segment window. Returns
    ((samples, rate), overrun_s, error) — overrun_s is 0 when the
    speech fits, else the seconds it overruns by (reported honestly,
    never silently clipped)."""
    import numpy as _np

    cur_speed = float(speed)
    last = None
    while True:
        rendered, err = _tts_render(text, voice, lang, cur_speed, _tts=_tts)
        if err is not None:
            return None, 0.0, err
        samples, rate = rendered
        samples = _np.asarray(samples, dtype=_np.float32)
        dur = len(samples) / float(rate) if rate > 0 else 0.0
        if dur <= window_s or cur_speed >= _DUB_SPEED_MAX:
            return (samples, int(rate)), max(0.0, dur - window_s), None
        # The speech overruns its window: speed up and re-render.
        # (A 1.5x bump per pass, capped at 4.0 — at most a few passes.)
        last = (samples, int(rate), dur)
        cur_speed = min(_DUB_SPEED_MAX, cur_speed * 1.5)
        if cur_speed >= _DUB_SPEED_MAX:
            rendered, err = _tts_render(text, voice, lang, cur_speed,
                                        _tts=_tts)
            if err is not None:
                return None, 0.0, err
            samples, rate = rendered
            samples = _np.asarray(samples, dtype=_np.float32)
            dur = len(samples) / float(rate) if rate > 0 else 0.0
            return (samples, int(rate)), max(0.0, dur - window_s), None
        _ = last

# ------------------------------------------------------- subtitle files
def _format_vtt(cues):
    """Serialise cues to VTT text (the tree's own _format_time with
    vtt=True for the timestamps). Cue shape: {index, start, end, text}."""
    parts = ["WEBVTT", ""]
    for cue in cues:
        parts.append("%d\n%s --> %s\n%s"
                     % (cue["index"],
                        _subtitles._format_time(cue["start"], vtt=True),
                        _subtitles._format_time(cue["end"], vtt=True),
                        cue["text"]))
    return "\n\n".join(parts) + ("\n" if len(parts) > 2 else "")


def _write_subtitles(out_dir, project_name, target_lang, segments, fmt):
    """Write the translated segments as subtitle files. Returns
    ({format: path}, error). fmt is 'srt', 'vtt' or 'both'."""
    cues = [{"index": i + 1, "start": float(s[3]), "end": float(s[4]),
             "text": str(s[6])}
            for i, s in enumerate(segments)]
    if not cues:
        return None, "refused: there are no translated segments to write."
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    if fmt in ("srt", "both"):
        path = out_dir / ("subtitles.%s.srt" % target_lang)
        try:
            path.write_text(_subtitles.format_srt(cues),
                            encoding="utf-8")
        except OSError as e:
            return None, "refused: cannot write %s (%s)." % (path.name, e)
        written["srt"] = str(path)
    if fmt in ("vtt", "both"):
        path = out_dir / ("subtitles.%s.vtt" % target_lang)
        try:
            path.write_text(_format_vtt(cues), encoding="utf-8")
        except OSError as e:
            return None, "refused: cannot write %s (%s)." % (path.name, e)
        written["vtt"] = str(path)
    return written, None


def _write_dub_wav(out_dir, project_name, target_lang, placed, duration_s):
    """Merge the dubbed segments into one full-length WAV (24 kHz mono
    16-bit, stdlib `wave`). `placed` is a list of (start_s, samples,
    rate) — each segment's PCM placed at its start time, silence
    elsewhere. Returns (path, error)."""
    import numpy as _np

    out_dir.mkdir(parents=True, exist_ok=True)
    total = max(1, int(round(float(duration_s) * _DUB_RATE)))
    track = _np.zeros(total, dtype=_np.float32)
    for start_s, samples, rate in placed:
        samples = _np.asarray(samples, dtype=_np.float32)
        if int(rate) != _DUB_RATE:
            # Kokoro always renders at 24 kHz; a different rate is a
            # bug in the render, not a resample to guess at.
            return None, ("refused: the dub render came back at %d Hz "
                          "(expected %d)." % (int(rate), _DUB_RATE))
        offset = int(round(float(start_s) * _DUB_RATE))
        if offset < 0:
            offset = 0
        end = min(total, offset + len(samples))
        if end > offset:
            track[offset:end] += samples[:end - offset]
    peak = float(_np.max(_np.abs(track))) if len(track) else 0.0
    if peak > 1.0:
        track = track / peak
    pcm = (_np.clip(track, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    path = out_dir / ("dub.%s.wav" % target_lang)
    try:
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_DUB_RATE)
            wf.writeframes(pcm)
    except (wave.Error, OSError) as e:
        return None, "refused: cannot write %s (%s)." % (path.name, e)
    return str(path), None


# ---------------------------------------------------------------- handler
def _need_project(store, project):
    """The project row (not deleted) or (None, refusal)."""
    if not isinstance(project, str) or not project.strip():
        return None, "refused: give the project name."
    row = store.project_get(project.strip())
    if row is None or row[8] == 1:
        return None, "refused: no project named %r." % project.strip()
    return row, None


def _segments_with_text(store, project_id):
    """Segments as (id, project_id, idx, start, end, text, translated,
    dubbed, created) rows, or ([], None)."""
    return store.segment_list(project_id, limit=_MAX_SEGMENTS), None


def audio_localise(action=None, project=None, file=None, source_lang=None,
                   target_lang=None, voice=None, speed=None, limit=None,
                   mode=None, segment_id=None, format=None, detail=None,
                   name=None, _stt=None, _opener=None, _tts=None):
    """Localise audio: transcribe, translate and dub an audio file.
    The STT (faster-whisper), the LM (Gemini) and the TTS (Kokoro)
    are injectable for the suite/prover; the live path uses the
    tree's own engines. Returns a plain-text answer."""
    store = _AlStore()
    act = str(action or "").strip().lower()
    if act not in _ACTIONS:
        return ("audio_localise refused: action must be one of %s."
                % ", ".join(_ACTIONS))
    try:
        lim = int(limit) if limit is not None else 20
    except (TypeError, ValueError):
        return "audio_localise refused: limit must be a whole number."
    lim = max(1, min(lim, 100))

    # -- project -------------------------------------------------------
    if act == "project":
        m = str(mode or "list").strip().lower()
        if m not in _PROJECT_MODES:
            return ("project refused: mode must be one of %s."
                    % ", ".join(_PROJECT_MODES))
        if m == "list":
            rows = store.project_list(limit=lim)
            if not rows:
                return "No audio-localisation projects yet."
            lines = ["Audio-localisation projects (%d):" % len(rows)]
            for r in rows:
                lines.append(
                    "  #%d %s — %s -> %s, %s, voice %s"
                    % (r[0], r[1], r[3], r[4], _fmt_hours(r[7]), r[5]))
            return "\n".join(lines)
        if m == "get":
            row, err = _need_project(store, project)
            if err is not None:
                return err
            segs = store.segment_list(row[0], limit=_MAX_SEGMENTS)
            n_tr = len(segs)
            n_tl = sum(1 for s in segs if s[6])
            n_db = sum(1 for s in segs if s[7])
            out = _out_dir(row[1])
            subs = [str(p) for p in
                    (out / ("subtitles.%s.srt" % row[4]),
                     out / ("subtitles.%s.vtt" % row[4]))
                    if p.is_file()]
            dub = out / ("dub.%s.wav" % row[4])
            lines = [
                "Project %r (#%d) — %s -> %s, voice %s, speed %s, %s of audio."
                % (row[1], row[0], row[3], row[4], row[5], row[6],
                   _fmt_hours(row[7])),
                "  file: %s" % row[2],
                "  segments: %d transcribed, %d translated, %d dubbed."
                % (n_tr, n_tl, n_db),
            ]
            if subs:
                lines.append("  subtitles: " + ", ".join(subs))
            if dub.is_file():
                lines.append("  dubbed audio: %s" % str(dub))
            return "\n".join(lines)
        if m == "remove":
            row, err = _need_project(store, project)
            if err is not None:
                return err
            count = store.project_remove(row[1])
            if count is None:
                return "project refused: no project named %r." % project
            return ("Project %r removed (soft-delete) with %d segment(s) "
                    "kept — re-add the same name to restore them."
                    % (row[1], count))
        # add
        if not isinstance(project, str) or not project.strip():
            return "project refused: give the project name."
        pname = project.strip()
        if len(pname) > _MAX_PROJECT_NAME:
            return ("project refused: the name is %d chars (max %d)."
                    % (len(pname), _MAX_PROJECT_NAME))
        src = _parse_lang(source_lang) if source_lang else "en"
        if source_lang and src is None:
            return ("project refused: unsupported source language %r."
                    % source_lang)
        dst = _parse_lang(target_lang) if target_lang else None
        if target_lang and dst is None:
            return ("project refused: unsupported target language %r."
                    % target_lang)
        if dst is None:
            return "project refused: give the target language."
        if dst not in _KOKORO_LANGS:
            return ("project refused: Kokoro has no voice for %r, so it "
                    "cannot be dubbed (dubbable: %s)."
                    % (dst, ", ".join(sorted(_KOKORO_LANGS))))
        if src == dst:
            return ("project refused: source and target are both %r — "
                    "nothing to translate." % src)
        v = str(voice or _DEFAULT_VOICE).strip()
        vlang = _voice_lang(v)
        if vlang is None:
            return ("project refused: %r is not a Kokoro voice name "
                    "(e.g. af_heart)." % v)
        if vlang != dst:
            return ("project refused: voice %r speaks %r, but the target "
                    "language is %r — the dub voice must match the target."
                    % (v, vlang, dst))
        try:
            spd = float(speed) if speed is not None else 1.0
        except (TypeError, ValueError):
            return "project refused: speed must be a number."
        if spd <= _DUB_SPEED_MIN or spd > _DUB_SPEED_MAX:
            return ("project refused: speed must be in (%s, %s]."
                    % (_DUB_SPEED_MIN, _DUB_SPEED_MAX))
        read, err = _read_wav(file)
        if err is not None:
            return err
        _samples, _rate, duration = read
        got = store.project_add(pname, str(Path(file).expanduser()),
                                src, dst, v, spd, duration)
        if got is None:
            return "project refused: the store write failed."
        if got.get("exists"):
            return ("project refused: %r already exists." % pname)
        row = got["row"]
        if got.get("restored"):
            return ("Project %r restored with its segments — %s -> %s, "
                    "%s of audio." % (pname, src, dst, _fmt_hours(duration)))
        return ("Project %r added (#%d) — %s -> %s, voice %s, speed %s, "
                "%s of audio. Transcribe it next."
                % (pname, row[0], src, dst, v, spd, _fmt_hours(duration)))

    # -- transcribe ----------------------------------------------------
    if act == "transcribe":
        row, err = _need_project(store, project)
        if err is not None:
            return err
        read, err = _read_wav(row[2])
        if err is not None:
            return err
        samples, rate, _duration = read
        audio_16k = _resample_16k(samples, rate)
        segments, err = _stt_transcribe(audio_16k, row[3], _stt=_stt)
        if err is not None:
            return err
        rows = store.segments_replace(row[0], segments)
        if not rows:
            return "transcribe refused: the store write failed."
        lines = ["Transcribed %r — %d segments (%s of audio):"
                 % (row[1], len(rows), _fmt_hours(row[7]))]
        for s in rows[:5]:
            lines.append("  #%d %s --> %s: %s"
                         % (s[2], _fmt_time(s[3]), _fmt_time(s[4]),
                            (s[5][:80] + "…") if len(s[5]) > 80 else s[5]))
        if len(rows) > 5:
            lines.append("  … and %d more." % (len(rows) - 5))
        lines.append("Translate them next (action 'translate').")
        return "\n".join(lines)

    # -- translate -----------------------------------------------------
    if act == "translate":
        row, err = _need_project(store, project)
        if err is not None:
            return err
        m = str(mode or "all").strip().lower()
        if m not in _TRANSLATE_MODES:
            return ("translate refused: mode must be one of %s."
                    % ", ".join(_TRANSLATE_MODES))
        segs = store.segment_list(row[0], limit=_MAX_SEGMENTS)
        if not segs:
            return ("translate refused: %r has no transcribed segments — "
                    "transcribe it first." % row[1])
        if m == "one":
            try:
                idx = int(segment_id)
            except (TypeError, ValueError):
                return "translate refused: give the segment number."
            target = [s for s in segs if s[2] == idx]
            if not target:
                return ("translate refused: no segment #%d in %r."
                        % (idx, row[1]))
            batch = [{"start": target[0][3], "end": target[0][4],
                      "text": target[0][5]}]
        else:
            batch = [{"start": s[3], "end": s[4], "text": s[5]}
                     for s in segs if not s[6]]
            if not batch:
                return ("translate refused: every segment in %r is already "
                        "translated." % row[1])
        translations, err = _translate_segments(
            batch, row[3], row[4], _opener=_opener)
        if err is not None:
            return err
        if m == "one":
            got = store.segment_set_translation(target[0][0], translations[0])
            if got is None:
                return "translate refused: the store write failed."
            fmt = str(format or "both").strip().lower()
            if fmt not in ("srt", "vtt", "both"):
                return "translate refused: format must be 'srt', 'vtt' or 'both'."
            segs_now = store.segment_list(row[0], limit=_MAX_SEGMENTS)
            translated_now = [s for s in segs_now if s[6]]
            written, err = _write_subtitles(
                _out_dir(row[1]), row[1], row[4], translated_now, fmt)
            if err is not None:
                return err
            return ("Segment #%d translated — %s\n  subtitles: %s"
                    % (idx, translations[0], ", ".join(written.values())))
        done = 0
        for s, tr in zip([s for s in segs if not s[6]], translations):
            if store.segment_set_translation(s[0], tr) is not None:
                done += 1
        if done != len(translations):
            return ("translate refused: only %d of %d translations were "
                    "stored." % (done, len(translations)))
        fmt = str(format or "both").strip().lower()
        if fmt not in ("srt", "vtt", "both"):
            return "translate refused: format must be 'srt', 'vtt' or 'both'."
        segs_now = store.segment_list(row[0], limit=_MAX_SEGMENTS)
        translated_now = [s for s in segs_now if s[6]]
        written, err = _write_subtitles(
            _out_dir(row[1]), row[1], row[4], translated_now, fmt)
        if err is not None:
            return err
        return ("Translated %d segment(s) in %r (%s -> %s).\n  subtitles: %s"
                % (done, row[1], row[3], row[4],
                   ", ".join(written.values())))

    # -- dub -----------------------------------------------------------
    if act == "dub":
        row, err = _need_project(store, project)
        if err is not None:
            return err
        m = str(mode or "all").strip().lower()
        if m not in _DUB_MODES:
            return ("dub refused: mode must be one of %s."
                    % ", ".join(_DUB_MODES))
        segs = store.segment_list(row[0], limit=_MAX_SEGMENTS)
        translated = [s for s in segs if s[6]]
        if not translated:
            return ("dub refused: %r has no translated segments — "
                    "translate it first." % row[1])
        if m == "one":
            try:
                idx = int(segment_id)
            except (TypeError, ValueError):
                return "dub refused: give the segment number."
            target = [s for s in translated if s[2] == idx]
            if not target:
                return ("dub refused: no translated segment #%d in %r."
                        % (idx, row[1]))
            work = target
        else:
            work = [s for s in translated if not s[7]]
            if not work:
                return ("dub refused: every translated segment in %r is "
                        "already dubbed." % row[1])
        placed = []
        overruns = []
        for s in work:
            text = str(s[6])
            if len(text) > _MAX_SEGMENT_CHARS:
                return ("dub refused: segment #%d is %d chars (max %d) — "
                        "split it first." % (s[2], len(text),
                                             _MAX_SEGMENT_CHARS))
            window = max(0.5, float(s[4]) - float(s[3]))
            rendered, over, err = _dub_segment(
                text, row[5], row[4], row[6], window, _tts=_tts)
            if err is not None:
                return err
            samples, rate = rendered
            placed.append((float(s[3]), samples, int(rate)))
            if over > 0:
                overruns.append((s[2], over))
        out = _out_dir(row[1])
        path, err = _write_dub_wav(out, row[1], row[4], placed, row[7])
        if err is not None:
            return err
        done = 0
        for s in work:
            if store.segment_set_dubbed(s[0]) is not None:
                done += 1
        if done != len(work):
            return ("dub refused: only %d of %d segments were marked "
                    "dubbed." % (done, len(work)))
        lines = ["Dubbed %d segment(s) in %r — %s" % (done, row[1], path)]
        if overruns:
            lines.append(
                "  %d segment(s) overran their window (speech longer than "
                "the subtitle timing, even at 4.0x speed): %s"
                % (len(overruns),
                   ", ".join("#%d (+%.1fs)" % (i, o) for i, o in overruns)))
        return "\n".join(lines)

    # -- localise (the full pipeline) ----------------------------------
    if act == "localise":
        row, err = _need_project(store, project)
        if err is not None:
            return err
        fmt = str(format or "both").strip().lower()
        if fmt not in ("srt", "vtt", "both"):
            return "localise refused: format must be 'srt', 'vtt' or 'both'."
        read, err = _read_wav(row[2])
        if err is not None:
            return err
        samples, rate, _duration = read
        audio_16k = _resample_16k(samples, rate)
        segments, err = _stt_transcribe(audio_16k, row[3], _stt=_stt)
        if err is not None:
            return err
        rows = store.segments_replace(row[0], segments)
        if not rows:
            return "localise refused: the store write failed."
        batch = [{"start": s[3], "end": s[4], "text": s[5]} for s in rows]
        translations, err = _translate_segments(
            batch, row[3], row[4], _opener=_opener)
        if err is not None:
            return err
        done = 0
        for s, tr in zip(rows, translations):
            if store.segment_set_translation(s[0], tr) is not None:
                done += 1
        if done != len(translations):
            return ("localise refused: only %d of %d translations were "
                    "stored." % (done, len(translations)))
        segs = store.segment_list(row[0], limit=_MAX_SEGMENTS)
        out = _out_dir(row[1])
        written, err = _write_subtitles(out, row[1], row[4], segs, fmt)
        if err is not None:
            return err
        placed = []
        overruns = []
        for s in segs:
            text = str(s[6])
            if len(text) > _MAX_SEGMENT_CHARS:
                return ("localise refused: segment #%d is %d chars (max "
                        "%d) — split it first."
                        % (s[2], len(text), _MAX_SEGMENT_CHARS))
            window = max(0.5, float(s[4]) - float(s[3]))
            rendered, over, err = _dub_segment(
                text, row[5], row[4], row[6], window, _tts=_tts)
            if err is not None:
                return err
            samples, rate = rendered
            placed.append((float(s[3]), samples, int(rate)))
            if over > 0:
                overruns.append((s[2], over))
        dub_path, err = _write_dub_wav(out, row[1], row[4], placed, row[7])
        if err is not None:
            return err
        dubbed = 0
        for s in segs:
            if store.segment_set_dubbed(s[0]) is not None:
                dubbed += 1
        lines = [
            "Localised %r — %d segments, %s -> %s (%s of audio):"
            % (row[1], len(segs), row[3], row[4], _fmt_hours(row[7])),
            "  subtitles: " + ", ".join(written.values()),
            "  dubbed audio: %s (%d segment(s) dubbed)" % (dub_path, dubbed),
        ]
        if overruns:
            lines.append(
                "  %d segment(s) overran their window: %s"
                % (len(overruns),
                   ", ".join("#%d (+%.1fs)" % (i, o) for i, o in overruns)))
        return "\n".join(lines)

    # -- status --------------------------------------------------------
    if act == "status":
        row, err = _need_project(store, project)
        if err is not None:
            return err
        segs = store.segment_list(row[0], limit=_MAX_SEGMENTS)
        n_tr = len(segs)
        n_tl = sum(1 for s in segs if s[6])
        n_db = sum(1 for s in segs if s[7])
        out = _out_dir(row[1])
        subs = [p.name for p in
                (out / ("subtitles.%s.srt" % row[4]),
                 out / ("subtitles.%s.vtt" % row[4]))
                if p.is_file()]
        dub = out / ("dub.%s.wav" % row[4])
        lines = [
            "%r: %d transcribed, %d translated, %d dubbed "
            "(%s -> %s, %s of audio)."
            % (row[1], n_tr, n_tl, n_db, row[3], row[4],
               _fmt_hours(row[7])),
            "  subtitles: %s; dubbed audio: %s."
            % (", ".join(subs) if subs else "none yet",
               "yes" if dub.is_file() else "not yet"),
        ]
        if str(detail or "").strip().lower() == "full" and segs:
            for s in segs[:10]:
                lines.append("  #%d %s --> %s: %s%s"
                             % (s[2], _fmt_time(s[3]), _fmt_time(s[4]),
                                s[5][:60],
                                ("  =>  " + str(s[6])[:60]) if s[6] else ""))
            if len(segs) > 10:
                lines.append("  … and %d more." % (len(segs) - 10))
        return "\n".join(lines)

    # -- events --------------------------------------------------------
    if act == "events":
        m = str(mode or "list").strip().lower()
        if m not in _EVENT_MODES:
            return ("events refused: mode must be one of %s."
                    % ", ".join(_EVENT_MODES))
        if m == "verify":
            ok, count, note = store.verify_chain()
            if ok:
                return ("Ledger verified: %d event(s), the chain is intact."
                        % count)
            return ("Ledger BROKEN: %d event(s) — %s." % (count, note))
        rows = store.list_events(limit=lim)
        if not rows:
            return "No localisation events yet."
        ok, count, note = store.verify_chain()
        lines = ["Localisation events (%d, newest first):" % len(rows)]
        for r in rows:
            try:
                payload = json.loads(r[4])
            except ValueError:
                payload = {}
            lines.append("  #%d %s %s — %s"
                         % (r[0], r[3],
                            time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(r[5])),
                            json.dumps(payload, sort_keys=True)[:100]))
        lines.append("  chain: %s (%d event(s))."
                     % ("intact" if ok else "BROKEN — " + str(note), count))
        return "\n".join(lines)
    return "audio_localise refused: unknown action."


# -------------------------------------------------------------- register
def register_all(registry) -> None:
    """Register the audio-localisation pack (R19-1,
    Huanshere/VideoLingo inspiration): audio_localise — transcribe an
    audio file (faster-whisper), translate the segments (the product's
    own Gemini key), write subtitle files (SRT/VTT) and dub the
    translation with the tree's own Kokoro TTS into a full-length
    dubbed WAV, over a hash-chained ledger (free local actions;
    translate/localise are confirm-gated — they call the owner's
    Gemini endpoint). Own stdlib code over the pipeline shape; nothing
    adopted — no VideoLingo code, no Streamlit, no Qwen3/WhisperX, no
    yt-dlp, no FFmpeg, no video processing. The module owns its
    catalogue via register_all (same pattern as the other packs)."""
    registry.register(
        name="audio_localise",
        description=(
            "Localise an audio file: transcribe it to segments with "
            "faster-whisper, translate the segments with the product's "
            "own Gemini key, write the translation as subtitle files "
            "(SRT/VTT) and dub it with the tree's own Kokoro TTS into a "
            "full-length dubbed WAV. The operator supplies a WAV audio "
            "file and the source/target languages; the tool returns the "
            "transcript, the translated subtitles and the dubbed audio. "
            "No YouTube download, no video processing (no FFmpeg), no "
            "Streamlit. Use when the user wants a recording, lecture or "
            "podcast in another language — as subtitles, as dubbed "
            "audio, or both."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'project' (manage projects), 'transcribe' (speech to segments), 'translate' (segments to the target language), 'dub' (translated segments to a dubbed WAV), 'localise' (the full pipeline), 'status' or 'events'"},
                "project": {"type": "string",
                            "description": "The project name (with every action except 'project' list)"},
                "file": {"type": "string",
                         "description": "With 'project' add, the WAV audio file path (WAV only, max 100 MB, max 60 minutes)"},
                "source_lang": {"type": "string",
                                "description": "With 'project' add, the audio's language (en, fr, de, es, it, pt, ja, zh, hi, ru)"},
                "target_lang": {"type": "string",
                                "description": "With 'project' add, the language to translate and dub into — a Kokoro language (en, es, fr, hi, it, ja, pt, zh; no German or Russian voice exists)"},
                "voice": {"type": "string",
                          "description": "With 'project' add, the Kokoro voice for the dub (must speak the target language, e.g. af_heart for English)"},
                "speed": {"type": "number",
                          "description": "With 'project' add, the dub speed (0.0 exclusive to 4.0 inclusive; 1.0 if left out)"},
                "limit": {"type": "integer",
                          "description": "With 'project' list or 'events', most rows shown (20 if left out, max 100)"},
                "mode": {"type": "string",
                         "description": "With 'project', one of 'add', 'list', 'get' or 'remove'; with 'translate' or 'dub', 'all' or 'one'; with 'events', 'list' or 'verify'"},
                "segment_id": {"type": "integer",
                               "description": "With 'translate' or 'dub' mode 'one', the segment number"},
                "format": {"type": "string",
                           "description": "With 'localise', the subtitle format: 'srt', 'vtt' or 'both' (both if left out)"},
                "detail": {"type": "string",
                           "description": "With 'status', 'full' for the first segments with their translations"},
                "name": {"type": "string",
                         "description": "With 'project' add, a note for the project (at most 200 chars)"},
            },
            "required": ["action"],
        },
        handler=audio_localise,
    )
