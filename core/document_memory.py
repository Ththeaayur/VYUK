"""Darwin D1 — document memory: the bridge from the disk index
into the KnowledgeStore.

The two substrates existed but were disconnected: core/disk_index.py
holds a ranked FTS5 index over user-chosen folders (keyword-only),
and core/memory/knowledge.py holds documents + vectors with a real
semantic lane (nothing ever fed disk files into it). This module
connects them: every file the disk index walks is also written to
the KnowledgeStore as chunked passages (doc_id ``disk:<hash>:<n>``),
so the same folder is searchable by meaning (``search_documents``)
and listed on the Memory page (``list_documents``).

Scope (brief ruling 3): text first — plain text plus Office
documents through the existing extraction path
(core/desktop/document_convert.py, proven real) and PDFs through
pypdf. Scanned documents and photos need OCR, and OCR is HELD:
``ocr_file`` (Tesseract via pytesseract) is unproven in the lab
(no pytesseract, no tesseract binary), so image files are skipped
with a named reason, never faked as indexed.

Discipline: opt-in stands — only user-chosen roots, no watcher,
no whole-disk sweep, documents never leave the PC. Incremental
like the disk index (a file is re-read only when its mtime or
size changed; vanished files leave both stores). The embedding
model stays operator-supplied — this module never downloads or
bundles weights; embedding runs through the KnowledgeStore's
``embed_missing`` (bounded, 50/call), which is honest when the
model is absent.
"""

import hashlib
import os
from pathlib import Path

from .disk_index import _load_roots, _readable_text, index_files
from .memory.knowledge import KnowledgeStore

# Plain text: mirrors disk_index._INDEX_EXTS — the FTS index and
# the document memory read the same files. Pinned by the D1 suite:
# if either side drifts, the test fails.
TEXT_EXTS = frozenset(
    {".txt", ".md", ".py", ".log", ".json", ".csv", ".ini"})
# Office, through the existing document_convert path (proven real).
OFFICE_EXTS = frozenset({".docx", ".pptx", ".xlsx"})
# PDFs, through pypdf (read_pdf_text's engine).
PDF_EXTS = frozenset({".pdf"})
# Images: OCR-held — skipped with a named reason, never indexed.
IMAGE_EXTS = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
     ".tif", ".tiff"})
DOCUMENT_EXTS = TEXT_EXTS | OFFICE_EXTS | PDF_EXTS | IMAGE_EXTS

# Passages are capped so one huge file cannot flood the store with
# a single row, and so a semantic hit points at a readable chunk.
CHUNK_CHARS = 2000
# A file larger than this is skipped, the same cap the disk index
# applies to text (its _readable_text enforces it for text; the
# Office/PDF legs enforce it here).
MAX_FILE_BYTES = 2 * 1024 * 1024

OCR_HELD_REASON = (
    "images are not indexed yet — OCR (Tesseract) is not "
    "available in this build, so scanned documents and photos "
    "are held, not faked as indexed")

# The owner's own files carry no third-party licence; "local"
# marks the provenance as this machine's documents.
LOCAL_LICENCE = "local"


def _store(db_path=None):
    return KnowledgeStore(db_path=db_path)


def doc_id_for(source, index):
    """Stable id for one chunk: the file's path hash + chunk
    number. The same file re-indexed after an edit gets the same
    ids, so a re-sync replaces rather than duplicates."""
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    return f"disk:{digest}:{index}"


def chunk_text(text):
    """Split text into passages of at most CHUNK_CHARS, at
    paragraph boundaries where possible; a single paragraph
    longer than the cap is hard-split. Empty passages are
    dropped."""
    if not text or not str(text).strip():
        return []
    chunks = []
    current = ""
    for paragraph in str(text).split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        while len(paragraph) > CHUNK_CHARS:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:CHUNK_CHARS])
            paragraph = paragraph[CHUNK_CHARS:].strip()
        if not paragraph:
            continue
        if current and len(current) + 2 + len(paragraph) > CHUNK_CHARS:
            chunks.append(current)
            current = paragraph
        elif current:
            current = current + "\n\n" + paragraph
        else:
            current = paragraph
    if current:
        chunks.append(current)
    return chunks


def extract_file_text(path):
    """One file's text, or (None, reason) with the reason it was
    skipped. Text uses the disk index's own reader (same cap,
    same binary probe); Office uses document_convert; PDFs use
    pypdf; images are OCR-held; anything else is unsupported."""
    path = Path(str(path))
    suffix = path.suffix.lower()
    if suffix in TEXT_EXTS:
        text = _readable_text(path)
        if text is None:
            return None, "binary or larger than 2 MB"
        return text, ""
    if suffix in IMAGE_EXTS:
        return None, OCR_HELD_REASON
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, f"unreadable: {exc}"
    if size > MAX_FILE_BYTES:
        return None, "larger than 2 MB"
    if suffix in OFFICE_EXTS:
        from .desktop import document_convert

        result = document_convert.convert(path)
        if not result.get("ok"):
            return None, str(result.get("reason") or
                            "Office conversion failed")
        text = str(result.get("markdown") or "")
        if not text.strip():
            return None, "no extractable text"
        return text, ""
    if suffix in PDF_EXTS:
        try:
            from pypdf import PdfReader
        except Exception:  # noqa: BLE001 — engine absent
            return None, ("PDF reading unavailable in this build "
                          "(pypdf not installed)")
        try:
            reader = PdfReader(str(path))
            pages = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001 — one bad page
                    continue
        except Exception as exc:  # noqa: BLE001 — unreadable PDF
            return None, f"PDF unreadable: {exc}"
        text = "\n".join(pages)
        if not text.strip():
            return None, "no extractable text"
        return text, ""
    return None, f"unsupported file type {suffix!r}"


def sync_file(path, stat=None, text=None, db_path=None):
    """Bring one file's stored passages up to date. `text` is the
    already-read text when the caller has it (the disk index's
    text files); otherwise the file is extracted here. Returns a
    record with a status in new / updated / unchanged / skipped
    and the passage count re-read from the store (A4-filestat)."""
    source = str(Path(str(path)))
    store = _store(db_path=db_path)
    try:
        stat = stat if stat is not None else Path(source).stat()
    except OSError as exc:
        return {"ok": False, "status": "skipped",
                "source": source, "passages": 0,
                "reason": f"unreadable: {exc}"}
    state = store.get_file_state(source)
    if (state is not None
            and state["mtime"] == stat.st_mtime
            and state["size"] == stat.st_size):
        return {"ok": True, "status": "unchanged",
                "source": source,
                "passages": int(state["passages"]), "reason": ""}
    if text is None:
        text, reason = extract_file_text(source)
        if text is None:
            # Was stored, now unextractable: the stale passages
            # leave the store rather than lying about content
            # that is no longer there.
            removed = store.delete_documents_by_source(source)
            return {"ok": True, "status": "skipped",
                    "source": source, "passages": 0,
                    "reason": reason,
                    "removed": removed["documents"]}
    chunks = chunk_text(text)
    if not chunks:
        removed = store.delete_documents_by_source(source)
        return {"ok": True, "status": "skipped",
                "source": source, "passages": 0,
                "reason": "no extractable text",
                "removed": removed["documents"]}
    status = "updated" if state is not None else "new"
    title = Path(source).name
    store.delete_documents_by_source(source)
    for index, passage in enumerate(chunks):
        store.add_document(doc_id_for(source, index), title, passage,
                           source=source, licence=LOCAL_LICENCE)
    verified = len(store.get_document_passages(source))
    store.set_file_state(source, stat.st_mtime, stat.st_size, verified)
    return {"ok": True, "status": status, "source": source,
            "passages": verified, "reason": ""}


def purge_file(path, db_path=None):
    """Remove one file's stored passages + bridge state.
    Returns the counts actually removed."""
    store = _store(db_path=db_path)
    removed = store.delete_documents_by_source(str(Path(str(path))))
    return {"ok": True, "source": str(path),
            "files": 1 if removed["documents"] else 0,
            "passages": removed["documents"],
            "vectors": removed["vectors"]}


def purge_vanished(seen, roots, db_path=None):
    """Remove bridge states for files under the indexed roots
    that the walk did not see (vanished since the last index).
    `seen` is the set of paths the walk visited this run."""
    store = _store(db_path=db_path)
    seen = set(str(p) for p in (seen or ()))
    files = 0
    passages = 0
    for state in store.list_file_states():
        source = state["source"]
        if source in seen:
            continue
        under_root = any(
            source == str(r) or source.startswith(str(r) + os.sep)
            for r in (roots or ()))
        if not under_root:
            continue
        removed = store.delete_documents_by_source(source)
        files += 1
        passages += removed["documents"]
    return {"files": files, "passages": passages}


def reset_document_store(db_path=None):
    """Drop every disk-bridged document + bridge state (the
    rebuild path: a full re-index re-syncs everything)."""
    store = _store(db_path=db_path)
    files = 0
    passages = 0
    for state in store.list_file_states():
        removed = store.delete_documents_by_source(state["source"])
        files += 1
        passages += removed["documents"]
    # Orphans: documents with no bridge state (a state row lost
    # under an older build) still leave the store.
    for entry in store.list_document_files(limit=10000):
        if store.get_file_state(entry["source"]) is None:
            removed = store.delete_documents_by_source(
                entry["source"])
            files += 1
            passages += removed["documents"]
    return {"files": files, "passages": passages}


def document_store_exists(db_path=None):
    """True when the knowledge DB file already exists. The Memory
    page checks this before listing, so merely opening the page
    never creates the store file (no knowledge.db exists in a
    fresh data/ until the first index or search)."""
    from .memory.knowledge import knowledge_db_path

    if db_path is not None:
        return Path(str(db_path)).exists()
    return Path(knowledge_db_path()).exists()


def list_documents(limit=10, offset=0, db_path=None):
    """The Memory page's Documents list: one row per file, with
    the embedding status so the page can say honestly whether
    semantic search is available."""
    store = _store(db_path=db_path)
    files = store.list_document_files(limit=limit, offset=offset)
    total_files = store.count_document_files()
    states = store.list_file_states()
    total_passages = sum(int(s["passages"]) for s in states)
    status = store.vector_index_status()
    return {"files": files, "total_files": total_files,
            "total_passages": total_passages,
            "model_ready": bool(status.get("model_ready")),
            "semantic_available": bool(
                status.get("semantic_available")),
            "reason": str(status.get("reason") or "")}


def search_documents(query, limit=10, db_path=None, embedder=None):
    """Semantic-first search over the user's documents only.
    Lanes disclosed; with no model the FTS lane runs and the
    record says semantics was unavailable — never a fabricated
    ranking."""
    store = _store(db_path=db_path)
    result = store.search_documents(query, limit=limit,
                                    embedder=embedder)
    status = store.vector_index_status()
    result["model_ready"] = bool(status.get("model_ready"))
    if not status.get("model_ready"):
        result["reason"] = str(status.get("reason") or
                               "embedding model absent")
    else:
        result["reason"] = ""
    return result


def get_document_passages(source, db_path=None):
    """Every stored passage for one file, in order — the
    per-document view."""
    store = _store(db_path=db_path)
    passages = store.get_document_passages(str(Path(str(source))))
    return {"source": str(source), "passages": passages,
            "count": len(passages)}


def forget_documents(source, db_path=None):
    """The document forget path (confirm-gated at the funnel).
    A file forgets that file; a folder forgets every document
    under it AND drops it from the remembered index roots, so a
    later refresh cannot re-index it. "Forget everything
    learned" stays facts-only — this is the document half."""
    source = str(Path(str(source or "")).expanduser())
    if not source:
        return {"ok": False, "reason": "no file or folder given"}
    path = Path(source)
    store = _store(db_path=db_path)
    if path.is_file():
        removed = store.delete_documents_by_source(source)
        return {"ok": True, "kind": "file", "source": source,
                "files_removed": 1 if removed["documents"] else 0,
                "passages_removed": removed["documents"],
                "vectors_removed": removed["vectors"],
                "verified_remaining": removed.get(
                    "verified_remaining", -1),
                "root_removed": False}
    if path.is_dir():
        removed = store.delete_documents_under_root(source)
        # Drop the root from the remembered index roots too.
        from .disk_index import remove_root

        root_removed = remove_root(source)
        return {"ok": True, "kind": "folder", "source": source,
                "files_removed": removed["files"],
                "passages_removed": removed["passages"],
                "vectors_removed": removed["vectors"],
                "root_removed": root_removed}
    return {"ok": False, "reason": f"no such file or folder: {source}"}


def document_memory_status(db_path=None):
    """Counts for the Memory page + the status tool: files,
    passages, vectors, model readiness, and the roots."""
    store = _store(db_path=db_path)
    status = store.vector_index_status()
    states = store.list_file_states()
    return {
        "files": store.count_document_files(),
        "passages": sum(int(s["passages"]) for s in states),
        "embedded": int(status.get("embedded", 0)),
        "total": int(status.get("total", 0)),
        "unembedded": int(status.get("unembedded", 0)),
        "model_ready": bool(status.get("model_ready")),
        "semantic_available": bool(status.get("semantic_available")),
        "reason": str(status.get("reason") or ""),
        "roots": _load_roots(),
    }


def index_folder(folder, db_path=None):
    """Index one folder into document memory (the GUI's "Add
    folder" path): the disk index walk + the bridge + the
    bounded embed pass, all through index_files."""
    return index_files(folders=str(folder or ""))


def refresh_documents(db_path=None):
    """Re-index the remembered roots (the GUI's Refresh path)."""
    return index_files(folders="")
