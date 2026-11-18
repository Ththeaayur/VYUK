"""R9-3 whoosh PORT: a persistent, ranked whole-disk content index.

The gap it closes: VYUK's only content search was the unindexed
600-file substring grep (`search_file_contents`,
core/skills_system2_processes.py:113 — os.walk, `_SEARCH_FILE_CAP =
600`, walk order, no ranking) and `find_files`
(core/skills_plus_apps_files.py:547 — name patterns only, also an
unindexed os.walk). The tree's only FTS is memory-only: the knowledge
store's `documents_fts` (core/memory/knowledge.py:217) and the
resilience pack's `docs_fts` index knowledge documents, never the
user's disk files. There was no persistent, ranked "find the file
that says X" anywhere in the tree.

Why a PORT, not an adoption of whoosh (mchaput/whoosh 2.7.4,
BSD-2-Clause, 458 KB wheel, zero dependencies — the wheel's own
METADATA has an empty Requires list, verified from the downloaded
wheel in the lab): the tree already has the engine. sqlite3 FTS5 is
stdlib, it is proven in-tree twice (the two indexes above), its
bm25() is real BM25 ranking, and it is maintained — while whoosh
has had no push since 2024-01-03. Adopting a stalled upstream for
a job the stdlib already does would repeat the fastembed mistake
(R8-1). Whoosh's licence was read from the repo LICENSE.txt
(BSD-2-Clause, Copyright 2011 Matt Chaput); nothing is adopted,
so no THIRD_PARTY row is owed.

Design (all three constraints from the brief):
- SCOPE IS USER-CHOSEN ROOTS, OPT-IN. Nothing is indexed until
  the user names folders. No auto-indexing, no whole-disk sweep,
  no background watcher. The roots are remembered in
  data/disk_index_roots.json (paths only) so a later index run can
  refresh them without being told again.
- REBUILDABLE AND REFRESHABLE. Indexing is incremental by default
  (a file is re-read only when its mtime or size changed) and a
  full rebuild is one flag away. Files that vanished since the
  last index are removed from the index on the next run, and a
  search never returns a dead path without saying so: every hit
  is checked against the disk and dead hits are dropped with a
  count in the result.
- RESULTS RANK. The search is FTS5 MATCH ordered by bm25(), with
  an FTS5 snippet per hit — not a grep in a trench coat.

The index lives in data/disk_index.sqlite3 (the tree's own data
dir, never sent anywhere, no network): a `files` table (path,
name, content, mtime, size, indexed_at) plus an external-content
FTS5 table `files_fts` over name + content, the same shape as the
knowledge store's documents_fts (content='files',
content_rowid='id'). Re-indexing a changed file is DELETE then
INSERT on both tables — FTS5 has no UPSERT-friendly unique key
(the resilience pack says so at core/resilience/resilience_offline.py:86).

What is indexed: plain-text files only, the same default extension
set as search_file_contents (.txt .md .py .log .json .csv .ini),
files up to 2 MB, binary files skipped (NUL byte in the first
4 KB, the same test search_file_contents uses). The index DB
itself and the roots file are never indexed. Folders find_files
already skips (.git, node_modules, __pycache__, .venv, AppData,
...) are skipped here too, using its own _FIND_SKIP_DIRS.

If this Python's sqlite3 has no FTS5, every tool refuses honestly
instead of pretending — the probe is the same one knowledge.py
uses (fts5_available, core/memory/knowledge.py:69).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from .config import DATA_DIR
from .skills_plus_apps_files import _FIND_EXPLICIT_MAX_DEPTH, _FIND_SKIP_DIRS

_INDEX_FILENAME = "disk_index.sqlite3"
_ROOTS_FILENAME = "disk_index_roots.json"

# Same defaults as search_file_contents (skills_system2_processes.py).
_INDEX_EXTS = {".txt", ".md", ".py", ".log", ".json", ".csv", ".ini"}
_INDEX_SIZE_CAP = 2 * 1024 * 1024  # skip files larger than 2 MB
_INDEX_PROBE_BYTES = 4096


def _index_db_path() -> Path:
    return Path(DATA_DIR) / _INDEX_FILENAME


def _roots_path() -> Path:
    return Path(DATA_DIR) / _ROOTS_FILENAME


def fts5_available() -> bool:
    """True when this Python's sqlite3 offers FTS5 (knowledge.py:69)."""
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE _t USING fts5(x)")
            return True
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        return False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_index_db_path()))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            indexed_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS files_fts
        USING fts5(name, content, content='files', content_rowid='id')
    """)
    conn.commit()


def _load_roots() -> list[str]:
    try:
        raw = json.loads(_roots_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if isinstance(p, str) and p.strip()]


def _save_roots(roots: list[str]) -> None:
    _roots_path().write_text(
        json.dumps(sorted(set(roots)), indent=2), encoding="utf-8"
    )


def _readable_text(path: Path) -> str | None:
    """File text, or None with the reason it was skipped (binary/large)."""
    try:
        if path.stat().st_size > _INDEX_SIZE_CAP:
            return None
        with open(path, "rb") as fh:
            head = fh.read(_INDEX_PROBE_BYTES)
            if b"\x00" in head:
                return None
            fh.seek(0)
            raw = fh.read()
    except OSError:
        return None
    return raw.decode("utf-8", errors="replace")


def _parse_folders(folders: str) -> list[str]:
    out: list[str] = []
    for part in (folders or "").split(","):
        part = part.strip()
        if part:
            out.append(part)
    return out


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return bool(value)


def _fts5_query(query: str) -> str | None:
    """A quoted AND of the query's words, safe for FTS5 MATCH.

    Each word is double-quoted so FTS5 operators inside the user's
    text (quotes, parens, *, ^) are data, never syntax. Words with
    no letters or digits are dropped; nothing usable left means
    there is no query to run.
    """
    terms: list[str] = []
    for word in (query or "").split():
        cleaned = "".join(
            ch for ch in word if ch.isalnum() or ch in ("_", "-")
        )
        if cleaned:
            terms.append('"' + cleaned.replace('"', "") + '"')
    if not terms:
        return None
    return " AND ".join(terms)


def _format_when(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return "unknown"


def index_files(folders: str = "", rebuild: bool = False) -> str:
    """Index the given folders (comma-separated) into the local FTS5 index.

    Incremental by default: a file is re-read only when its mtime or
    size changed since the last index run. Pass rebuild=true for a
    full re-index. The folders are remembered as the index roots, so
    a later run with no folders refreshes them.
    """
    if not fts5_available():
        return ("I can't build the search index on this machine — "
                "this Python's SQLite has no FTS5, so there is no "
                "ranked index to build. The live scan "
                "(search_file_contents) still works.")
    wanted = _parse_folders(folders)
    remembered = _load_roots()
    roots = sorted(set(remembered) | set(wanted))
    if not roots:
        return ("Tell me which folder to index, e.g. index_files with "
                "folders='C:\\Users\\you\\Documents'. Nothing is indexed "
                "until you choose the folders.")
    bad: list[str] = []
    good: list[Path] = []
    for root in roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            bad.append(root)
        else:
            good.append(base)
    if not good:
        if bad:
            return ("Couldn't index — these don't exist as folders "
                    "here: " + ", ".join(bad))
        return "Nothing to index — no usable folder was given."
    db_path = _index_db_path()
    rebuild = _coerce_bool(rebuild)
    conn = _connect()
    try:
        _ensure_schema(conn)
        before_total = conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]
        if rebuild:
            conn.execute("DELETE FROM files_fts")
            conn.execute("DELETE FROM files")
            conn.commit()
            before_total = 0
        indexed_roots_before = 0
        for (old_path,) in conn.execute(
            "SELECT path FROM files"
        ).fetchall():
            if any(
                old_path == str(b) or old_path.startswith(str(b) + os.sep)
                for b in good
            ):
                indexed_roots_before += 1
        new = updated = unchanged = removed = skipped = 0
        seen: set[str] = set()
        now = time.time()
        for base in good:
            base_str = str(base)
            for dirpath, dirnames, filenames in os.walk(base):
                depth = len(Path(dirpath).relative_to(base).parts)
                if depth >= _FIND_EXPLICIT_MAX_DEPTH:
                    dirnames[:] = []
                dirnames[:] = sorted(
                    d for d in dirnames if d not in _FIND_SKIP_DIRS
                )
                for fname in sorted(filenames):
                    fpath = Path(dirpath) / fname
                    fkey = str(fpath)
                    if fkey == str(db_path) or fkey == str(_roots_path()):
                        continue
                    if fpath.suffix.lower() not in _INDEX_EXTS:
                        continue
                    try:
                        stat = fpath.stat()
                    except OSError:
                        skipped += 1
                        continue
                    seen.add(fkey)
                    row = conn.execute(
                        "SELECT id, mtime, size FROM files WHERE path = ?",
                        (fkey,),
                    ).fetchone()
                    if (
                        row is not None
                        and row[1] == stat.st_mtime
                        and row[2] == stat.st_size
                    ):
                        unchanged += 1
                        continue
                    text = _readable_text(fpath)
                    if text is None:
                        skipped += 1
                        if row is not None:
                            # Was indexed, now unreadable (binary or
                            # too large): the stale entry leaves the
                            # index rather than lying about content
                            # that is no longer there.
                            conn.execute(
                                "DELETE FROM files_fts WHERE rowid = ?",
                                (row[0],),
                            )
                            conn.execute(
                                "DELETE FROM files WHERE id = ?", (row[0],)
                            )
                            removed += 1
                        continue
                    if row is not None:
                        conn.execute(
                            "DELETE FROM files_fts WHERE rowid = ?",
                            (row[0],),
                        )
                        conn.execute(
                            "UPDATE files SET name = ?, content = ?, "
                            "mtime = ?, size = ?, indexed_at = ? "
                            "WHERE id = ?",
                            (fname, text, stat.st_mtime, stat.st_size,
                             now, row[0]),
                        )
                        conn.execute(
                            "INSERT INTO files_fts (rowid, name, content) "
                            "VALUES (?, ?, ?)",
                            (row[0], fname, text),
                        )
                        updated += 1
                    else:
                        cursor = conn.execute(
                            "INSERT INTO files (path, name, content, "
                            "mtime, size, indexed_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (fkey, fname, text, stat.st_mtime,
                             stat.st_size, now),
                        )
                        conn.execute(
                            "INSERT INTO files_fts (rowid, name, content) "
                            "VALUES (?, ?, ?)",
                            (cursor.lastrowid, fname, text),
                        )
                        new += 1
        # Files that vanished since the last index leave the index.
        for (old_path,) in conn.execute("SELECT path FROM files").fetchall():
            if old_path not in seen and any(
                old_path == str(b) or old_path.startswith(str(b) + os.sep)
                for b in good
            ):
                row = conn.execute(
                    "SELECT id FROM files WHERE path = ?", (old_path,)
                ).fetchone()
                if row is not None:
                    conn.execute(
                        "DELETE FROM files_fts WHERE rowid = ?", (row[0],)
                    )
                conn.execute("DELETE FROM files WHERE path = ?", (old_path,))
                removed += 1
        conn.commit()
        # A4-filestat: the count is re-read before the success line
        # names any number — a write that did not land is reported
        # as a failure, never claimed. Expected: files outside the
        # indexed roots (untouched) + the files indexed under the
        # indexed roots this run (new + updated + unchanged).
        after_total = conn.execute(
            "SELECT COUNT(*) FROM files"
        ).fetchone()[0]
        expected = (
            (before_total - indexed_roots_before)
            + new + updated + unchanged
        )
        if after_total != expected:
            return ("The index write didn't land as expected "
                    f"(counted {after_total} files, expected {expected}) "
                    "— nothing is claimed. Try index_files again with "
                    "rebuild=true.")
    finally:
        conn.close()
    _save_roots([str(b) for b in good] + [r for r in remembered])
    parts = [f"Indexed {new + updated} file(s)"]
    if new:
        parts.append(f"{new} new")
    if updated:
        parts.append(f"{updated} updated")
    if unchanged:
        parts.append(f"{unchanged} unchanged")
    if removed:
        parts.append(f"{removed} removed (no longer on disk)")
    if skipped:
        parts.append(f"{skipped} skipped (binary, too large, or unreadable)")
    summary = ", ".join(parts)
    result = (f"{summary}. The index now holds {after_total} file(s) "
              f"across {len(roots)} folder(s).")
    if bad:
        result += (" Note: these folders don't exist right now and "
                   "were skipped: " + ", ".join(bad) + ".")
    return result


def search_indexed_files(query: str = "", limit: int = 10) -> str:
    """Ranked search over the indexed files (FTS5 bm25, best first)."""
    needle = (query or "").strip()
    if not needle:
        return ("Tell me what text to look for, e.g. "
                "search_indexed_files with query='invoice total'.")
    if not fts5_available():
        return ("I can't search the index on this machine — this "
                "Python's SQLite has no FTS5. The live scan "
                "(search_file_contents) still works.")
    match = _fts5_query(needle)
    if match is None:
        return ("That query has no searchable words in it — give me a "
                "word or two from the file you're looking for.")
    db_path = _index_db_path()
    if not db_path.exists():
        return ("Nothing is indexed yet — index a folder first with "
                "index_files, or use search_file_contents for a live "
                "scan of a folder.")
    try:
        cap = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        cap = 10
    conn = _connect()
    try:
        _ensure_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        if total == 0:
            return ("Nothing is indexed yet — index a folder first with "
                    "index_files, or use search_file_contents for a live "
                    "scan of a folder.")
        rows = conn.execute(
            "SELECT f.path, bm25(files_fts) AS rank, "
            "snippet(files_fts, 1, '', '', ' … ', 24) AS snip "
            "FROM files_fts JOIN files f ON f.id = files_fts.rowid "
            "WHERE files_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, cap + 10),
        ).fetchall()
    except sqlite3.OperationalError:
        return ("The index query failed — the index may be from an "
                "older build. Rebuild it with index_files and "
                "rebuild=true.")
    finally:
        conn.close()
    if not rows:
        return (f"No indexed file mentions '{needle}'. The index holds "
                f"{total} file(s) — try different words, or refresh it "
                "with index_files.")
    lines: list[str] = []
    dead = 0
    for path, _rank, snip in rows:
        if not Path(path).exists():
            dead += 1
            continue
        snippet = " ".join((snip or "").split())
        lines.append(f"{path} — {snippet}" if snippet else path)
        if len(lines) >= cap:
            break
    if not lines:
        return (f"The index mentions '{needle}' but every hit is a file "
                "that no longer exists — refresh the index with "
                "index_files and try again.")
    head = (f"Found {len(lines)} indexed file(s) mentioning '{needle}' "
            "(best match first):")
    if dead:
        head += (f" {dead} indexed hit(s) were files that no longer "
                 "exist and were left out — re-run index_files to "
                 "refresh.")
    return head + "\n" + "\n".join(
        f"{i + 1}. {line}" for i, line in enumerate(lines)
    )


def disk_index_status() -> str:
    """What the disk index holds: roots, file count, size, last run."""
    if not fts5_available():
        return ("The disk index is unavailable on this machine — this "
                "Python's SQLite has no FTS5, so no ranked index can "
                "be built here. The live scan (search_file_contents) "
                "still works.")
    db_path = _index_db_path()
    roots = _load_roots()
    if not db_path.exists():
        if roots:
            return ("The disk index has folders tracked ("
                    + ", ".join(roots)
                    + ") but no index file yet — run index_files to "
                    "build it.")
        return ("Nothing is indexed yet — index a folder first with "
                "index_files, or use search_file_contents for a live "
                "scan of a folder.")
    conn = _connect()
    try:
        _ensure_schema(conn)
        total = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        last = conn.execute(
            "SELECT MAX(indexed_at) FROM files"
        ).fetchone()[0]
    finally:
        conn.close()
    try:
        size = db_path.stat().st_size
    except OSError:
        size = 0
    if total == 0:
        return ("The disk index exists but holds no files — run "
                "index_files on a folder to fill it.")
    last_text = _format_when(last) if last else "unknown"
    return (f"Disk index: {total} file(s) across {len(roots)} folder(s)"
            + (f" ({', '.join(roots)})" if roots else "")
            + f", index file {size} bytes, last indexed {last_text}.")
