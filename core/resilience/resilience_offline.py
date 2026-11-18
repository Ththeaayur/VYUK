"""RL-5.x offline depth: a floor, not a download manager (wave 25, §5.18).

- RL-5.3 FTS5 knowledge pack (native, stdlib sqlite3 FTS5, OWN file):
  the lite tier's labelled knowledge floor. Cached answers must never
  be mistakable for facts in the audit trail — every served answer
  carries its cached-at label, and search results carry it too.
- RL-5.2 resumable download (native, stdlib urllib, Range + hash
  verify — the pypdl PATTERN, not the package): one attempt per call,
  no hidden retry loop. A partial file resumes on the caller's next
  invocation via the Range header; the helper itself never re-attempts.
- RL-5.1 huggingface_hub telemetry hard-off: WRITTEN RULE ONLY. The
  library is not a current dependency, so applying hard-off code now
  would be dead code. The rule below is the env/config shape to apply
  IF huggingface_hub is ever added. Per-model weights licences remain
  owed at selection time (§7.0 rule).
- RL-5.4 Kiwix: bundle REJECTED (GPL family end-to-end). The sidecar
  shape below is SPEC ONLY: the user installs Kiwix and downloads ZIMs
  themselves; VYUK would talk to separately-installed ``kiwix-serve``
  over localhost HTTP — no linking, no distribution by VYUK. Legal
  sign-off still owed; no sidecar code ships here. ZIM content is
  CC BY-SA: VYUK must not redistribute content, and ZIMs are full
  snapshots (no incremental updates — budget full-file replacement and
  stale-content labelling).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import urllib.request

from .. import store_base
from datetime import datetime, timezone

FTS5_FILENAME = "rl_knowledge.sqlite3"

# RL-5.1 written rule only (see module docstring): apply this env
# shape IF huggingface_hub is ever added. Nothing applies it today.
HF_TELEMETRY_OFF_ENV = {"HF_HUB_DISABLE_TELEMETRY": "1"}

# RL-5.4 sidecar SPEC only (see module docstring): no code talks to
# kiwix-serve in this build, and VYUK distributes neither Kiwix nor ZIMs.
KIWIX_SIDECAR_SPEC = {
    "shape": "user-installed sidecar",
    "user_installs": ["Kiwix application", "ZIM files (user downloads)"],
    "vyuk_talks_to": "separately-installed kiwix-serve over localhost HTTP",
    "linking": "none",
    "distribution_by_vyuk": "none (no Kiwix, no ZIMs)",
    "content_licence": "CC BY-SA (no redistribution by VYUK)",
    "updates": "full-file replacement only (ZIMs are snapshots)",
    "stale_content": "must be labelled with its snapshot date",
    "legal_signoff": "still owed",
}


def _apply_pragmas(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")


class KnowledgePack:
    """RL-5.3 FTS5 knowledge pack in its OWN file (INV-8).

    ``docs`` holds the canonical rows (doc_id, title, cached_at);
    ``docs_fts`` is the FTS5 index over title + body. Every served
    answer carries its cached-at label in the returned dict, so a
    cached answer can never be mistaken for a live fact downstream.
    """

    def __init__(self, db_path: str):
        store_base.refuse_assistant_db(db_path, what="knowledge pack")
        self.db_path = str(db_path)
        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        try:
            _apply_pragmas(con)
            con.execute(
                "CREATE TABLE IF NOT EXISTS docs ("
                "doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
                "cached_at TEXT NOT NULL)")
            # Plain FTS5 table (doc_id carried UNINDEXED so the index
            # joins back to docs for the cached-at label). Re-adds
            # DELETE then INSERT: FTS5 has no UPSERT-friendly unique
            # constraint, so replacement is explicit, never assumed.
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING "
                "fts5(doc_id UNINDEXED, title, body)")
            con.commit()
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        _apply_pragmas(con)
        return con

    def add_doc(self, doc_id: str, title: str, body: str,
                cached_at: str | None = None) -> None:
        """Store a document with its cached-at label (UTC ISO by
        default). Re-adding a doc_id replaces it, keeping one row."""
        label = cached_at or datetime.now(timezone.utc).isoformat()
        con = self._connect()
        try:
            con.execute(
                "INSERT INTO docs (doc_id, title, cached_at) VALUES "
                "(?, ?, ?) ON CONFLICT(doc_id) DO UPDATE SET "
                "title = excluded.title, cached_at = excluded.cached_at",
                (doc_id, title, label))
            con.execute("DELETE FROM docs_fts WHERE doc_id = ?",
                        (doc_id,))
            con.execute(
                "INSERT INTO docs_fts (doc_id, title, body) VALUES "
                "(?, ?, ?)", (doc_id, title, body))
            con.commit()
        finally:
            con.close()

    def search(self, query: str, limit: int = 10) -> list:
        """FTS5 search. Every hit carries its cached-at label."""
        phrase = '"' + query.replace('"', '""') + '"'
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT d.doc_id, d.title, d.cached_at FROM docs d "
                "JOIN docs_fts f ON f.doc_id = d.doc_id "
                "WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?",
                (phrase, limit)).fetchall()
        finally:
            con.close()
        return [{"doc_id": r[0], "title": r[1], "cached_at": r[2]}
                for r in rows]

    def serve(self, doc_id: str) -> dict | None:
        """Serve a document WITH its cached-at label (never bare)."""
        con = self._connect()
        try:
            row = con.execute(
                "SELECT d.title, d.cached_at, f.body FROM docs d "
                "JOIN docs_fts f ON f.doc_id = d.doc_id "
                "WHERE d.doc_id = ?", (doc_id,)).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {"doc_id": doc_id, "title": row[0], "cached_at": row[1],
                "body": row[2],
                "cached_label": "Cached answer — cached at %s; not a "
                "live fact." % row[1]}


def download_resumable(url: str, dest_path: str,
                       expected_sha256: str | None = None,
                       timeout_s: float = 60.0) -> dict:
    """RL-5.2 resumable download: ONE attempt, no hidden retry loop.

    If ``dest_path`` already holds a partial file, the request carries
    ``Range: bytes=<existing>-`` and appends; if the server answers 200
    (Range ignored), the file restarts from zero. On success the SHA-256
    is verified when ``expected_sha256`` is given; a mismatch reports
    "hash-mismatch" and the file is NOT presented as complete. On any
    transport error the helper returns an "error" verdict with the bytes
    written so far — the CALLER may invoke again to resume. That
    caller-driven resume is not a retry layer: this function never
    re-attempts on its own.
    """
    directory = os.path.dirname(os.path.abspath(dest_path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    existing = 0
    if os.path.isfile(dest_path):
        existing = os.path.getsize(dest_path)
    request = urllib.request.Request(url)
    if existing > 0:
        request.add_header("Range", "bytes=%d-" % existing)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            status = resp.status
            mode = "ab" if (status == 206 and existing > 0) else "wb"
            written = existing if mode == "ab" else 0
            with open(dest_path, mode) as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
    except Exception as exc:  # noqa: BLE001 — transport error is a verdict
        return {"status": "error", "error": str(exc),
                "bytes_written": existing,
                "dest": dest_path}
    digest = None
    if expected_sha256 is not None:
        hasher = hashlib.sha256()
        with open(dest_path, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 256)
                if not chunk:
                    break
                hasher.update(chunk)
        digest = hasher.hexdigest()
        if digest != expected_sha256.lower():
            return {"status": "hash-mismatch", "bytes": written,
                    "sha256": digest, "sha256_ok": False,
                    "dest": dest_path}
    return {"status": "complete", "bytes": written,
            "sha256": digest, "sha256_ok": True,
            "dest": dest_path}


# RL-7.4: model files are hash-verified at startup; a mismatch is
# reported as corrupt-or-quarantined, never silently trusted. Models
# are never served from %TEMP% (AV quarantine/scan churn lives there),
# and an AV exclusion is a USER action — never added programmatically.
MODELS_TEMP_EXCLUSION = (
    "models are never served from %TEMP%; AV exclusion is a "
    "USER action, never added programmatically")


def verify_model_file(path: str, expected_sha256: str,
                      max_lock_waits: int = 3) -> dict:
    """RL-7.4 AV hash verdict for one model file at startup.

    Returns {"verdict", "hash_ok", "sha256", "path"}. Verdicts: "clean"
    (hash matches), "corrupt-or-quarantined" (hash mismatches — the
    file may be corrupt or AV-quarantined; either way it is NOT
    trusted), "missing" (no such file), "locked" (still unreadable
    after the bounded wait below). A locked file gets a single bounded
    wait loop (backoff sleeps, not a call-retry layer — the file is
    never re-requested, only re-opened while an AV scan holds it).
    """
    import time

    if not os.path.isfile(path):
        return {"verdict": "missing", "hash_ok": False, "sha256": None,
                "path": path}
    data = None
    last_error = None
    for wait in range(max_lock_waits + 1):
        try:
            with open(path, "rb") as handle:
                data = handle.read()
            break
        except OSError as exc:  # noqa: BLE001 — locked file is a verdict
            last_error = exc
            if wait < max_lock_waits:
                time.sleep(0.05 * (2 ** wait))
    if data is None:
        return {"verdict": "locked", "hash_ok": False, "sha256": None,
                "path": path, "error": str(last_error)}
    digest = hashlib.sha256(data).hexdigest()
    if digest == expected_sha256.lower():
        return {"verdict": "clean", "hash_ok": True, "sha256": digest,
                "path": path}
    return {"verdict": "corrupt-or-quarantined", "hash_ok": False,
            "sha256": digest, "path": path}
