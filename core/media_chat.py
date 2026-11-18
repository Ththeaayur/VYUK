"""Chat with your media (R19-4, nexmoe/VidBee inspiration).

VidBee is a desktop app that downloads video/audio from 1000+ sites
(yt-dlp), transcribes locally with speaker labels (diarization), and
lets the user summarize, translate, or chat with the media using
their preferred AI provider. This module builds the honest buildable
remainder in own stdlib code: CHAT WITH YOUR MEDIA — Q&A and summary
over a transcript, answered by the product's own configured Gemini
key (the prompt_tune _lm_call shape). The transcript comes from an
audio_localise project (transcribe first, then link it here) or from
an operator-supplied transcript file (SRT / VTT / plain text). The
answers are stored with the transcript's hash, so every answer is
attributable to the exact transcript version it was drawn from, and
the LM is told to answer ONLY from the transcript and to say so when
the transcript does not contain the answer.

STOPs, stated on the tin:
  * NO DOWNLOADER. No yt-dlp download, no YouTube download, no site
    download. The tree's yt-dlp clearance (BR-2) is library-only for
    music PLAYBACK, gated; extending it from playback to download is
    a ToS/scope question the parent rules, and this batch does not
    build it. The tool works on operator-supplied media and
    transcripts only — a file the operator already has, or a
    transcript the operator already made (audio_localise).
  * NO SPEAKER DIARIZATION / NO SPEAKER LABELS. pyannote needs torch,
    which is BLOCKED. A torch-free route EXISTS in principle (ONNX
    speaker embeddings — speakeronnx / wespeaker-resnet34 under
    onnxruntime + numpy only, Apache-2.0 code), but the model
    licences are unverified and the segmentation + embedding +
    clustering pipeline is a follow-up batch, not this one. Nothing
    here labels a speaker.
  * NO VIDEO PROCESSING, no FFmpeg, no Streamlit.
  * NO TRANSLATION here — audio_localise already translates; use it.

Classification: `ask` and `summarize` call the owner's Gemini
endpoint and spend their LM quota, so they are confirm-gated (the
audio_localise translate/localise precedent, C-14 approve-first).
`add`, `list`, `get`, `remove`, `status` and `events` are local
app-state and reads — those stay free. Unknown or missing action
gates fail-closed (the handler refuses them anyway).

Bounds: transcript max 200,000 chars, max 500 segments; transcript
file max 2 MB; question max 1,000 chars; the LM prompt carries at
most 60,000 chars of transcript (a longer transcript is truncated
at that budget and the record says so); media name max 100 chars;
note max 200 chars.

Licence: MIT — "Parent-ruled (licence, 2026-11-12)" (the VidBee LICENSE, read
in full from main: 1,063 bytes, 21 lines, "Copyright (c) 2025
VidBee"; no code adopted, name not used).
"""

import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from . import store_base
from .prompt_tune import _lm_call as _gemini_call

_DB_NAME = "media_chat.sqlite3"

_MAX_TRANSCRIPT_CHARS = 200_000
_MAX_SEGMENTS = 500
_MAX_FILE_BYTES = 2_000_000
_MAX_QUESTION_CHARS = 1_000
_MAX_PROMPT_TRANSCRIPT_CHARS = 60_000
_MAX_DISPLAY_CHARS = 8_000
_MAX_NAME_CHARS = 100
_MAX_NOTE_CHARS = 200
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

_LOCK = threading.RLock()

_ASK_PROMPT = (
    "You are answering questions about a transcript. Answer ONLY from "
    "the transcript below. If the transcript does not contain the "
    "answer, say so plainly and do not guess, do not invent a speaker, "
    "and do not add facts from outside the transcript.\n\n"
    "Transcript (truncated to {budget} characters; the full transcript "
    "has {chars} characters):\n{transcript}\n\n"
    "Question: {question}\n\nAnswer:"
)
_SUMMARISE_SHORT_PROMPT = (
    "Summarize the transcript below in one short paragraph. Stay "
    "strictly to what the transcript says; do not add facts from "
    "outside it.\n\n"
    "Transcript (truncated to {budget} characters; the full transcript "
    "has {chars} characters):\n{transcript}\n\nSummary:"
)
_SUMMARISE_LONG_PROMPT = (
    "Summarize the transcript below as a structured summary: the main "
    "points in order, then any key quotes with their timestamps. Stay "
    "strictly to what the transcript says; do not add facts from "
    "outside it.\n\n"
    "Transcript (truncated to {budget} characters; the full transcript "
    "has {chars} characters):\n{transcript}\n\nSummary:"
)


def _db_path():
    return Path(DATA_DIR) / _DB_NAME


def _fmt_mmss(seconds):
    seconds = max(0.0, float(seconds or 0.0))
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return "%02d:%02d" % (mins, secs)


def _parse_cue_time(text):
    """Parse an SRT/VTT cue timestamp to seconds, or None."""
    m = re.match(r"^(\d+):(\d{2}):(\d{2})[,.](\d{3})$",
                 str(text).strip())
    if not m:
        m = re.match(r"^(\d{2}):(\d{2})[,.](\d{3})$", str(text).strip())
        if not m:
            return None
        mins, secs, ms = (int(m.group(1)), int(m.group(2)),
                          int(m.group(3)))
        return mins * 60 + secs + ms / 1000.0
    hours, mins, secs, ms = (int(m.group(1)), int(m.group(2)),
                             int(m.group(3)), int(m.group(4)))
    return hours * 3600 + mins * 60 + secs + ms / 1000.0


def _parse_cues(text):
    """Parse SRT/VTT cue blocks into [{start, end, text}]. A block
    whose second line is not a timestamp is kept as untimed text
    (start/end 0.0) rather than dropped — the words are the
    transcript, the timing is a bonus."""
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", str(text).strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
        if not lines:
            continue
        if lines[0].strip().upper().startswith("NOTE"):
            continue
        start = end = 0.0
        text_lines = lines
        if len(lines) >= 2 and "-->" in lines[1]:
            parts = lines[1].split("-->")
            parsed_start = _parse_cue_time(parts[0])
            parsed_end = _parse_cue_time(parts[1])
            if parsed_start is not None and parsed_end is not None:
                start, end = parsed_start, parsed_end
                text_lines = lines[2:]
            else:
                text_lines = lines[2:]
        elif len(lines) >= 2 and "-->" in lines[0]:
            parts = lines[0].split("-->")
            parsed_start = _parse_cue_time(parts[0])
            parsed_end = _parse_cue_time(parts[1])
            if parsed_start is not None and parsed_end is not None:
                start, end = parsed_start, parsed_end
                text_lines = lines[1:]
        body = " ".join(ln.strip() for ln in text_lines if ln.strip())
        if body:
            cues.append({"start": start, "end": end, "text": body})
    return cues


def _cues_to_transcript(cues):
    """One '[mm:ss] text' line per cue — the stored transcript IS
    the timestamped text, so the LM prompt uses it directly."""
    return "\n".join("[%s] %s" % (_fmt_mmss(c["start"]), c["text"])
                     for c in cues)


def _read_transcript_file(path):
    """Read an operator-supplied transcript file. Returns
    (transcript, segments, error). SRT/VTT are parsed as cues;
    plain text is taken as-is. Bounds are enforced before anything
    is stored."""
    p = Path(str(path))
    if not p.exists() or not p.is_file():
        return None, 0, "refused: no such file: %s" % p
    try:
        size = p.stat().st_size
    except OSError as exc:
        return None, 0, "refused: cannot stat %s (%s)" % (p, exc)
    if size > _MAX_FILE_BYTES:
        return None, 0, ("refused: the file is %d bytes, over the "
                         "2 MB transcript-file limit." % size)
    try:
        raw = p.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as exc:
        return None, 0, "refused: cannot read %s as UTF-8 text (%s)" % (p, exc)
    suffix = p.suffix.lower()
    if suffix in (".srt", ".vtt"):
        cues = _parse_cues(raw)
        if not cues:
            return None, 0, ("refused: no cues could be parsed from %s — "
                             "it does not look like an SRT/VTT file." % p)
        if len(cues) > _MAX_SEGMENTS:
            return None, 0, ("refused: %d cues, over the 500-segment "
                             "limit." % len(cues))
        transcript = _cues_to_transcript(cues)
        if len(transcript) > _MAX_TRANSCRIPT_CHARS:
            return None, 0, ("refused: the transcript is %d characters, "
                             "over the 200,000-character limit."
                             % len(transcript))
        return transcript, len(cues), None
    if suffix in (".txt", ".md", ""):
        transcript = raw.strip()
        if not transcript:
            return None, 0, "refused: the file is empty."
        if len(transcript) > _MAX_TRANSCRIPT_CHARS:
            return None, 0, ("refused: the transcript is %d characters, "
                             "over the 200,000-character limit."
                             % len(transcript))
        segments = len([ln for ln in transcript.splitlines() if ln.strip()])
        if segments > _MAX_SEGMENTS:
            return None, 0, ("refused: %d lines, over the 500-segment "
                             "limit." % segments)
        return transcript, max(1, segments), None
    return None, 0, ("refused: %s is not a transcript file "
                     "(SRT, VTT or plain text only)." % p.suffix)


def _transcript_from_project(project_name):
    """Build a transcript from an audio_localise project. Returns
    (transcript, segments, error). The project must exist, not be
    deleted, and already have segments — transcription itself stays
    in audio_localise (run transcribe first, then link it here)."""
    try:
        from . import audio_localise as _al
    except Exception as exc:  # noqa: BLE001 — the reason IS the refusal
        return None, 0, "refused: audio_localise is unavailable (%s)." % exc
    store = _al._AlStore()
    row = store.project_get(project_name)
    if row is None:
        return None, 0, ("refused: no audio_localise project called "
                         "'%s'." % project_name)
    if row[8]:
        return None, 0, ("refused: the audio_localise project '%s' "
                         "is deleted." % project_name)
    segments = store.segment_list(row[0], limit=_MAX_SEGMENTS + 1)
    if not segments:
        return None, 0, ("refused: the project '%s' has no transcript "
                         "yet — run audio_localise transcribe first."
                         % project_name)
    if len(segments) > _MAX_SEGMENTS:
        return None, 0, ("refused: the project has %d segments, over "
                         "the 500-segment limit." % len(segments))
    cues = [{"start": float(s[3]), "end": float(s[4]), "text": str(s[5])}
            for s in segments if str(s[5]).strip()]
    if not cues:
        return None, 0, ("refused: the project '%s' has no transcript "
                         "text." % project_name)
    transcript = _cues_to_transcript(cues)
    if len(transcript) > _MAX_TRANSCRIPT_CHARS:
        return None, 0, ("refused: the transcript is %d characters, "
                         "over the 200,000-character limit."
                         % len(transcript))
    return transcript, len(cues), None


def _transcript_hash(transcript):
    return hashlib.sha256(str(transcript).encode("utf-8")).hexdigest()


class _McStore(store_base.StoreBase):
    """Media items + Q&A + summaries + hash-chained event ledger.
    Open-per-operation; every write is followed by a re-read before
    the caller claims it (A1-store). A read (list/get/status/events)
    must not create the database — _missing() answers empty without
    touching the file."""

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else _db_path()


    def _connect(self):
        conn = store_base.connect(
            str(self.path), pragmas=("PRAGMA journal_mode=WAL",))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS media ("
            " id INTEGER PRIMARY KEY,"
            " name TEXT NOT NULL UNIQUE,"
            " source TEXT NOT NULL,"
            " source_ref TEXT NOT NULL,"
            " note TEXT NOT NULL,"
            " transcript TEXT NOT NULL,"
            " segments INTEGER NOT NULL,"
            " chars INTEGER NOT NULL,"
            " deleted INTEGER NOT NULL DEFAULT 0,"
            " created REAL NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS qa ("
            " id INTEGER PRIMARY KEY,"
            " media_id INTEGER NOT NULL,"
            " question TEXT NOT NULL,"
            " answer TEXT NOT NULL,"
            " transcript_hash TEXT NOT NULL,"
            " created REAL NOT NULL)")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS summaries ("
            " id INTEGER PRIMARY KEY,"
            " media_id INTEGER NOT NULL,"
            " length TEXT NOT NULL,"
            " summary TEXT NOT NULL,"
            " transcript_hash TEXT NOT NULL,"
            " created REAL NOT NULL)")
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

    # -- media ---------------------------------------------------------
    def media_add(self, name, source, source_ref, note, transcript,
                  segments):
        with _LOCK:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO media (name, source, source_ref, note,"
                    " transcript, segments, chars, created)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, source, source_ref, note, transcript, segments,
                     len(transcript), time.time()))
                media_id = cur.lastrowid
                self._append_event_locked(
                    conn, "media_add",
                    {"name": name, "source": source,
                     "source_ref": source_ref, "segments": segments,
                     "chars": len(transcript)})
                conn.commit()
                row = conn.execute(
                    "SELECT id, name, source, source_ref, note,"
                    " transcript, segments, chars, deleted, created"
                    " FROM media WHERE id = ?", (media_id,)).fetchone()
                return row
            finally:
                conn.close()

    def media_get(self, name):
        if self._missing():
            return None
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, name, source, source_ref, note,"
                " transcript, segments, chars, deleted, created"
                " FROM media WHERE name = ?", (name,)).fetchone()
        finally:
            conn.close()

    def media_list(self, limit=20):
        if self._missing():
            return []
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, name, source, source_ref, note,"
                " transcript, segments, chars, deleted, created"
                " FROM media WHERE deleted = 0 ORDER BY id LIMIT ?",
                (int(limit),)).fetchall()
        finally:
            conn.close()

    def media_remove(self, name):
        with _LOCK:
            if self._missing():
                return None
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT id FROM media WHERE name = ? AND deleted = 0",
                    (name,)).fetchone()
                if row is None:
                    return None
                conn.execute("UPDATE media SET deleted = 1 WHERE id = ?",
                             (row[0],))
                self._append_event_locked(
                    conn, "media_remove", {"name": name})
                conn.commit()
                return conn.execute(
                    "SELECT id, name, source, source_ref, note,"
                    " transcript, segments, chars, deleted, created"
                    " FROM media WHERE id = ?", (row[0],)).fetchone()
            finally:
                conn.close()

    # -- qa + summaries --------------------------------------------------
    def qa_add(self, media_id, question, answer, transcript_hash):
        with _LOCK:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO qa (media_id, question, answer,"
                    " transcript_hash, created) VALUES (?, ?, ?, ?, ?)",
                    (media_id, question, answer, transcript_hash,
                     time.time()))
                qa_id = cur.lastrowid
                self._append_event_locked(
                    conn, "qa",
                    {"media_id": media_id, "question": question[:200],
                     "transcript_hash": transcript_hash[:16]})
                conn.commit()
                return conn.execute(
                    "SELECT id, media_id, question, answer,"
                    " transcript_hash, created FROM qa WHERE id = ?",
                    (qa_id,)).fetchone()
            finally:
                conn.close()

    def qa_list(self, media_id, limit=20):
        if self._missing():
            return []
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, media_id, question, answer,"
                " transcript_hash, created FROM qa"
                " WHERE media_id = ? ORDER BY id LIMIT ?",
                (media_id, int(limit))).fetchall()
        finally:
            conn.close()

    def summary_add(self, media_id, length, summary, transcript_hash):
        with _LOCK:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO summaries (media_id, length, summary,"
                    " transcript_hash, created) VALUES (?, ?, ?, ?, ?)",
                    (media_id, length, summary, transcript_hash,
                     time.time()))
                summary_id = cur.lastrowid
                self._append_event_locked(
                    conn, "summary",
                    {"media_id": media_id, "length": length,
                     "transcript_hash": transcript_hash[:16]})
                conn.commit()
                return conn.execute(
                    "SELECT id, media_id, length, summary,"
                    " transcript_hash, created FROM summaries"
                    " WHERE id = ?", (summary_id,)).fetchone()
            finally:
                conn.close()

    def summary_list(self, media_id, limit=20):
        if self._missing():
            return []
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, media_id, length, summary,"
                " transcript_hash, created FROM summaries"
                " WHERE media_id = ? ORDER BY id LIMIT ?",
                (media_id, int(limit))).fetchall()
        finally:
            conn.close()

    def count_qa(self, media_id):
        if self._missing():
            return 0
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM qa WHERE media_id = ?",
                (media_id,)).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    def count_summaries(self, media_id):
        if self._missing():
            return 0
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM summaries WHERE media_id = ?",
                (media_id,)).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()

    # -- ledger ----------------------------------------------------------
    def list_events(self, limit=20):
        if self._missing():
            return []
        conn = self._connect()
        try:
            return conn.execute(
                "SELECT id, prev_hash, hash, kind, payload, created"
                " FROM events ORDER BY id LIMIT ?",
                (int(limit),)).fetchall()
        finally:
            conn.close()

    def verify_chain(self):
        if self._missing():
            return True, "no ledger yet", 0
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
                return (False,
                        "event #%d breaks the chain (prev_hash mismatch)"
                        % bad[0], len(rows))
            return (False,
                    "event #%d fails its hash" % bad[0], len(rows))
        return True, "intact", len(rows)


# ------------------------------------------------------------------- LM
def _prompt_transcript(transcript):
    """The transcript slice the LM sees, plus whether it was cut."""
    if len(transcript) <= _MAX_PROMPT_TRANSCRIPT_CHARS:
        return transcript, False
    return transcript[:_MAX_PROMPT_TRANSCRIPT_CHARS], True


def _ask(transcript, question, _opener=None):
    """Ask one question over a transcript with the product's own
    configured Gemini key (the prompt_tune _lm_call shape). Returns
    (answer, error, truncated) — the answer is None on refusal."""
    if not str(question).strip():
        return None, "refused: give the question.", False
    if len(str(question)) > _MAX_QUESTION_CHARS:
        return None, ("refused: the question is %d characters, over the "
                      "1,000-character limit." % len(question)), False
    sliced, truncated = _prompt_transcript(transcript)
    prompt = _ASK_PROMPT.format(
        budget=_MAX_PROMPT_TRANSCRIPT_CHARS,
        chars=len(transcript), transcript=sliced,
        question=str(question).strip())
    text, err = _gemini_call(prompt, None, _opener=_opener)
    if err is not None:
        return None, err, truncated
    if text is None or not str(text).strip():
        return None, ("refused: the answer came back empty. "
                      "Nothing was stored."), truncated
    return str(text).strip(), None, truncated


def _summarize(transcript, length, _opener=None):
    """Summarize one transcript with the product's own configured
    Gemini key. Returns (summary, error, truncated)."""
    sliced, truncated = _prompt_transcript(transcript)
    template = (_SUMMARISE_LONG_PROMPT if length == "long"
                else _SUMMARISE_SHORT_PROMPT)
    prompt = template.format(
        budget=_MAX_PROMPT_TRANSCRIPT_CHARS,
        chars=len(transcript), transcript=sliced)
    text, err = _gemini_call(prompt, None, _opener=_opener)
    if err is not None:
        return None, err, truncated
    if text is None or not str(text).strip():
        return None, ("refused: the summary came back empty. "
                      "Nothing was stored."), truncated
    return str(text).strip(), None, truncated


def _need_media(store, media):
    if not media or not str(media).strip():
        return None, "refused: give the media name."
    row = store.media_get(str(media).strip())
    if row is None:
        return None, "refused: no media called '%s'." % media
    if row[8]:
        return None, "refused: the media '%s' is removed." % media
    return row, None


# ---------------------------------------------------------------- handler
def media_chat(action=None, media=None, project=None, file=None, mode=None,
               question=None, length=None, name=None, limit=None,
               detail=None, _opener=None):
    """Chat with your media: register a transcript (from an
    audio_localise project or an operator-supplied SRT/VTT/plain-text
    file), then ask questions or request a summary over it, answered
    by the product's own Gemini key. Every answer and summary is
    stored with the transcript's hash, so it is attributable to the
    exact transcript version it was drawn from."""
    action = str(action or "").strip().lower()
    store = _McStore()
    if action == "add":
        media_name = str(media or "").strip()
        if not media_name:
            return "media_chat refused: give the media name."
        if len(media_name) > _MAX_NAME_CHARS:
            return ("media_chat refused: the media name is %d characters, "
                    "over the 100-character limit." % len(media_name))
        note = str(name or "").strip()[:_MAX_NOTE_CHARS]
        mode = str(mode or "").strip().lower()
        if mode == "project":
            if not project or not str(project).strip():
                return ("media_chat refused: give the audio_localise "
                        "project name.")
            transcript, segments, err = _transcript_from_project(
                str(project).strip())
            if err is not None:
                return "media_chat %s" % err
            source, source_ref = "audio_localise", str(project).strip()
        elif mode == "file":
            if not file or not str(file).strip():
                return "media_chat refused: give the transcript file path."
            transcript, segments, err = _read_transcript_file(
                str(file).strip())
            if err is not None:
                return "media_chat %s" % err
            source, source_ref = "file", str(file).strip()
        else:
            return ("media_chat refused: give the mode — 'project' (link "
                    "an audio_localise project) or 'file' (read an "
                    "operator-supplied transcript file).")
        if store.media_get(media_name) is not None:
            return ("media_chat refused: media called '%s' already "
                    "exists." % media_name)
        try:
            row = store.media_add(media_name, source, source_ref, note,
                                  transcript, segments)
        except sqlite3.IntegrityError:
            return ("media_chat refused: media called '%s' already "
                    "exists." % media_name)
        if row is None:
            return ("media_chat refused: the media item was not stored — "
                    "the write did not read back.")
        return ("media_chat: '%s' added (%s, %d segment(s), %d "
                "character(s)). Ask it a question with action 'ask', or "
                "summarize it with action 'summarize'."
                % (row[1], row[2], row[6], row[7]))
    if action == "ask":
        row, err = _need_media(store, media)
        if err is not None:
            return "media_chat %s" % err
        answer, err, truncated = _ask(row[5], question, _opener=_opener)
        if err is not None:
            return "media_chat %s" % err
        digest = _transcript_hash(row[5])
        saved = store.qa_add(row[0], str(question).strip(), answer, digest)
        if saved is None:
            return ("media_chat refused: the answer was not stored — "
                    "the write did not read back.")
        note = (" (the transcript was longer than the 60,000-character "
                "prompt budget, so the answer used the first 60,000 "
                "characters)" if truncated else "")
        return ("media_chat: '%s' — %s%s" % (row[1], answer, note))
    if action == "summarize":
        row, err = _need_media(store, media)
        if err is not None:
            return "media_chat %s" % err
        length = str(length or "short").strip().lower()
        if length not in ("short", "long"):
            return ("media_chat refused: length must be 'short' or "
                    "'long'.")
        summary, err, truncated = _summarize(row[5], length,
                                             _opener=_opener)
        if err is not None:
            return "media_chat %s" % err
        digest = _transcript_hash(row[5])
        saved = store.summary_add(row[0], length, summary, digest)
        if saved is None:
            return ("media_chat refused: the summary was not stored — "
                    "the write did not read back.")
        note = (" (the transcript was longer than the 60,000-character "
                "prompt budget, so the summary used the first 60,000 "
                "characters)" if truncated else "")
        return ("media_chat: '%s' (%s summary) — %s%s"
                % (row[1], length, summary, note))
    if action == "list":
        rows = store.media_list(limit=_clamp_limit(limit))
        if not rows:
            return "media_chat: no media registered yet."
        lines = ["media_chat: %d item(s):" % len(rows)]
        for r in rows:
            lines.append("  '%s' (%s: %s%s) — %d segment(s), %d char(s), "
                         "%d question(s), %d summar(ies)"
                         % (r[1], r[2], r[3],
                            (" — " + r[4]) if r[4] else "",
                            r[6], r[7], store.count_qa(r[0]),
                            store.count_summaries(r[0])))
        return "\n".join(lines)
    if action == "get":
        row, err = _need_media(store, media)
        if err is not None:
            return "media_chat %s" % err
        mode = str(mode or "transcript").strip().lower()
        if mode == "qa":
            rows = store.qa_list(row[0], limit=_clamp_limit(limit))
            if not rows:
                return "media_chat: '%s' has no questions yet." % row[1]
            lines = ["media_chat: '%s' — %d question(s):" % (row[1],
                                                             len(rows))]
            for q in rows:
                lines.append("  Q: %s\n  A: %s" % (q[2], q[3]))
            return "\n".join(lines)
        if mode == "summary":
            rows = store.summary_list(row[0], limit=_clamp_limit(limit))
            if not rows:
                return "media_chat: '%s' has no summaries yet." % row[1]
            lines = ["media_chat: '%s' — %d summar(ies):" % (row[1],
                                                             len(rows))]
            for s in rows:
                lines.append("  [%s] %s" % (s[2], s[3]))
            return "\n".join(lines)
        if mode != "transcript":
            return ("media_chat refused: mode must be 'transcript', 'qa' "
                    "or 'summary'.")
        text = row[5]
        shown = (text if len(text) <= _MAX_DISPLAY_CHARS
                 else text[:_MAX_DISPLAY_CHARS] + "\n… (truncated)")
        return ("media_chat: '%s' (%s: %s%s) — %d segment(s), %d char(s), "
                "%d question(s), %d summar(ies):\n%s"
                % (row[1], row[2], row[3],
                   (" — " + row[4]) if row[4] else "", row[6], row[7],
                   store.count_qa(row[0]), store.count_summaries(row[0]),
                   shown))
    if action == "remove":
        row, err = _need_media(store, media)
        if err is not None:
            return "media_chat %s" % err
        removed = store.media_remove(row[1])
        if removed is None or not removed[8]:
            return ("media_chat refused: '%s' was not removed — the "
                    "write did not read back." % row[1])
        return "media_chat: '%s' removed." % row[1]
    if action == "status":
        rows = store.media_list(limit=_MAX_LIMIT)
        ok, note, count = store.verify_chain()
        lines = ["media_chat: %d media item(s), ledger %s (%d event(s))."
                 % (len(rows), "intact" if ok else "BROKEN — " + str(note),
                    count)]
        lines.append("  No downloader, no speaker diarization, no video "
                     "processing — transcripts come from audio_localise "
                     "projects or operator-supplied SRT/VTT/text files.")
        if str(detail or "").strip().lower() == "full" and rows:
            for r in rows[:5]:
                lines.append("  '%s' — %d question(s), %d summar(ies)"
                             % (r[1], store.count_qa(r[0]),
                                store.count_summaries(r[0])))
        return "\n".join(lines)
    if action == "events":
        mode = str(mode or "list").strip().lower()
        if mode == "verify":
            ok, note, count = store.verify_chain()
            return ("media_chat: ledger %s (%d event(s))."
                    % ("intact" if ok else "BROKEN — " + str(note), count))
        if mode != "list":
            return ("media_chat refused: mode must be 'list' or "
                    "'verify'.")
        rows = store.list_events(limit=_clamp_limit(limit))
        if not rows:
            return "media_chat: no events yet."
        lines = ["media_chat: %d event(s):" % len(rows)]
        for r in rows:
            try:
                payload = json.loads(r[4])
            except (ValueError, TypeError):
                payload = {}
            lines.append("  #%d %s %s — %s"
                         % (r[0], r[3],
                            time.strftime("%Y-%m-%d %H:%M",
                                          time.localtime(r[5])),
                            json.dumps(payload, sort_keys=True)[:100]))
        ok, note, count = store.verify_chain()
        lines.append("  chain: %s (%d event(s))."
                     % ("intact" if ok else "BROKEN — " + str(note), count))
        return "\n".join(lines)
    return "media_chat refused: unknown action."


def _clamp_limit(limit):
    try:
        value = int(limit) if limit is not None else _DEFAULT_LIMIT
    except (TypeError, ValueError):
        value = _DEFAULT_LIMIT
    return max(1, min(_MAX_LIMIT, value))


# -------------------------------------------------------------- register
def register_all(registry) -> None:
    """Register the media-chat pack (R19-4, nexmoe/VidBee
    inspiration): media_chat — chat with your media: register a
    transcript (from an audio_localise project or an
    operator-supplied SRT/VTT/plain-text file), then ask questions
    or request a summary over it, answered by the product's own
    Gemini key, over a hash-chained ledger (ask/summarize are
    confirm-gated — they call the owner's Gemini endpoint; the
    local actions and the reads stay free). Own stdlib code over the
    Q&A shape; nothing adopted — no VidBee code, no yt-dlp download,
    no diarization, no video processing, no FFmpeg. The module owns
    its catalogue via register_all (same pattern as the other
    packs)."""
    registry.register(
        name="media_chat",
        description=(
            "Chat with your media: register a transcript — from an "
            "audio_localise project or an operator-supplied SRT, VTT "
            "or plain-text file — then ask questions about it or "
            "request a summary, answered by the product's own Gemini "
            "key from the transcript alone. The operator supplies the "
            "media or the transcript; the tool never downloads video "
            "or audio, never labels speakers, and never processes "
            "video. Use when the user wants to ask questions about a "
            "recording, lecture or podcast, or to summarize one."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "description": "One of: 'add' (register a transcript), 'ask' (ask a question over it), 'summarize' (summarize it), 'list', 'get', 'remove', 'status' or 'events'"},
                "media": {"type": "string",
                          "description": "The media name (with every action except 'add' list); with 'add', the name to register it under (max 100 chars)"},
                "project": {"type": "string",
                            "description": "With 'add' mode 'project', the audio_localise project name to link"},
                "file": {"type": "string",
                         "description": "With 'add' mode 'file', the operator-supplied transcript file path (SRT, VTT or plain text, max 2 MB)"},
                "mode": {"type": "string",
                         "description": "With 'add', 'project' or 'file'; with 'get', 'transcript', 'qa' or 'summary'; with 'events', 'list' or 'verify'"},
                "question": {"type": "string",
                             "description": "With 'ask', the question (max 1,000 chars) — answered only from the transcript"},
                "length": {"type": "string",
                           "description": "With 'summarize', 'short' (one paragraph) or 'long' (structured, with key points and quotes)"},
                "name": {"type": "string",
                         "description": "With 'add', a note for the media item (at most 200 chars)"},
                "limit": {"type": "integer",
                          "description": "With 'list', 'get' or 'events', most rows shown (20 if left out, max 100)"},
                "detail": {"type": "string",
                           "description": "With 'status', 'full' for the first media items with their question and summary counts"},
            },
            "required": ["action"],
        },
        handler=media_chat,
    )
