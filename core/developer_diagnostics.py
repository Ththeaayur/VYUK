"""DV-5 diagnostics (Developer track, wave 28) — developer-side
machinery, never bundled into the product unless an entry says so.

The shelf's field anchor is her own worst symptom class: the
long-unresponsive-while-connected session, where the logs never
answered the only question that mattered — what was the process
actually doing? This module is Nova's own answer to that class:

- Error-fingerprint store (pattern): normalise a traceback
  (exception type + app-owned frames; strip line numbers, paths,
  IDs), SHA-256 it into a local SQLite/JSONL store with
  first-seen / last-seen / count / build-version — and her doctor
  can answer "have I seen this failure before, and in which build
  did it start?" with ZERO telemetry. The hosted error-grouping
  shape, rebuilt as a local file.
- Log slicing: structured-field slicing over JSONL audit logs
  (the high-speed viewer shape) and a small query language over
  free-text logs (the jq-for-logs shape), simplified to stdlib.
- Field-diagnostics playbook (written, not code): three canned
  captures — hang, startup failure, file-access mystery — for the
  failures only the OS itself can see. Captures are collected by
  EXPLICIT instruction only, size/time-bounded, and sensitive
  (paths/usernames in captures are handled as sensitive data).
- Hang report: the narrative shape a sampling profiler gives
  ("what the interpreter was doing, as a stack, not a guess"),
  built from provided stack text — this library never attaches
  to a live process itself.

Dev/diagnostic use only. Nothing here phones home, uploads a
report, or runs by default in the shipped product.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3

from . import store_base

FRAME_RE = re.compile(
    r'File "(?P<file>[^"]+)", line \d+, in (?P<func>\S+)')
PATH_TOKEN_RE = re.compile(r"[A-Za-z]:\\[^\s\"']+|/[^\s\"']+")
ID_TOKEN_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|\b\d{6,}\b")

CAPTURE_PLAYBOOK = (
    {"name": "hang",
     "tools": ("os-native profiler", "process monitor"),
     "bounds": "60s capture, explicit instruction only",
     "sensitivity": "stacks + paths are sensitive; handle as logs",
     "admin_required": True},
    {"name": "startup failure",
     "tools": ("os-native profiler", "process monitor"),
     "bounds": "from launch to first window or 30s, explicit only",
     "sensitivity": "paths/usernames in captures are sensitive",
     "admin_required": False},
    {"name": "file-access mystery",
     "tools": ("process monitor",),
     "bounds": "single operation window, explicit instruction only",
     "sensitivity": "captured paths are sensitive; minimise retention",
     "admin_required": True},
)


class DebugError(ValueError):
    """Raised when a diagnostic record is malformed."""


def normalize_traceback(traceback_text):
    """Normalise a traceback for fingerprinting.

    Keeps: exception type (last line) + app-owned frames
    (function names only). Strips: line numbers, file paths,
    numeric IDs. Two runs of the same failure normalise identically
    even when paths and line numbers differ.
    """
    text = str(traceback_text)
    lines = text.strip().splitlines()
    exception_line = lines[-1].strip() if lines else ""
    frames = []
    for match in FRAME_RE.finditer(text):
        func = match.group("func")
        frames.append(func)
    normalised = exception_line + "|" + ",".join(frames)
    normalised = PATH_TOKEN_RE.sub("<path>", normalised)
    normalised = ID_TOKEN_RE.sub("<id>", normalised)
    return normalised


def fingerprint_traceback(traceback_text):
    """SHA-256 fingerprint of a normalised traceback (16 hex chars
    as the short form; full digest kept alongside in the store)."""
    normalised = normalize_traceback(traceback_text)
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return {"normalised": normalised, "sha256": digest,
            "short": digest[:16]}


class FingerprintStore:
    """Local error-fingerprint store (SQLite, zero telemetry).

    First-seen / last-seen / count / build-version per fingerprint.
    The store file is a diagnostic artefact with the same handling
    rules as logs: collected on instruction, not by default.
    """

    def __init__(self, db_path):
        if store_base.is_assistant_db(db_path):
            raise DebugError(
                "fingerprint store must never be the memory store")
        self.db_path = str(db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS fingerprints ("
                "short TEXT PRIMARY KEY, sha256 TEXT, normalised TEXT,"
                "first_seen REAL, last_seen REAL, count INTEGER,"
                "first_build TEXT, last_build TEXT)")
            conn.commit()
        finally:
            conn.close()

    def record(self, traceback_text, build_version, now):
        fp = fingerprint_traceback(traceback_text)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT count FROM fingerprints WHERE short=?",
                (fp["short"],)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO fingerprints VALUES (?,?,?,?,?,?,?,?)",
                    (fp["short"], fp["sha256"], fp["normalised"],
                     now, now, 1, build_version, build_version))
            else:
                conn.execute(
                    "UPDATE fingerprints SET last_seen=?, count=?, "
                    "last_build=? WHERE short=?",
                    (now, row[0] + 1, build_version, fp["short"]))
            conn.commit()
        finally:
            conn.close()
        return fp["short"]

    def lookup(self, traceback_text):
        fp = fingerprint_traceback(traceback_text)
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT short, first_seen, last_seen, count, "
                "first_build, last_build FROM fingerprints WHERE short=?",
                (fp["short"],)).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"seen": False, "short": fp["short"]}
        return {"seen": True, "short": row[0], "first_seen": row[1],
                "last_seen": row[2], "count": row[3],
                "first_build": row[4], "last_build": row[5]}


def slice_jsonl_by_field(jsonl_text, field, value):
    """Structured-field slicing over JSONL audit logs: return the
    entries whose ``field`` equals ``value``. Malformed lines are
    skipped, never fatal."""
    out = []
    for line in str(jsonl_text).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get(field) == value:
            out.append(entry)
    return out


def query_free_text(log_text, needle, context_lines=0):
    """Small free-text query over unstructured logs: lines
    containing ``needle``, with optional surrounding context."""
    lines = str(log_text).splitlines()
    hits = []
    for index, line in enumerate(lines):
        if needle in line:
            start = max(0, index - context_lines)
            end = min(len(lines), index + context_lines + 1)
            hits.append({"line": index + 1, "text": line,
                         "context": lines[start:end]})
    return hits


def capture_playbook(name):
    """Return a canned capture spec by name, or raise.

    Captures are collected by EXPLICIT instruction only — this
    function returns the spec; it never starts a capture, and no
    caller may treat the spec as consent.
    """
    for spec in CAPTURE_PLAYBOOK:
        if spec["name"] == name:
            return dict(spec)
    raise DebugError("unknown capture playbook: %r" % name)


def hang_report(stack_text, process_state):
    """Narrative hang report from provided stack text.

    Answers "what was the process doing?" as a stack summary, not
    a guess. ``process_state`` is caller-observed ("connected",
    "unresponsive", ...). This library never attaches to a live
    process; the stack text comes from the caller's own tooling.
    """
    frames = []
    for line in str(stack_text).splitlines():
        line = line.strip()
        if line:
            frames.append(line)
    return {"state": process_state, "frames": frames,
            "frame_count": len(frames),
            "narrative": ("process observed %s; %d stack frames "
                          "supplied by caller tooling"
                          % (process_state, len(frames)))}
