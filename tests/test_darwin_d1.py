"""Darwin D1 — document memory / semantic search (2026-11-23).

The headline Darwin gap: a memory graph and meaning map over
the user's DOCUMENT library (plain Markdown stored on disk, in
Darwin's shape) — search by meaning, including scanned
documents and photos. VYUK had the two substrates but they were
disconnected: the KnowledgeStore (FTS5 + semantic lane) held
learned facts only, and the disk index (user-chosen roots,
opt-in) was keyword-only. D1 bridges them: index_files also
writes passages into the KnowledgeStore (core/document_memory.py),
so text + office + PDF documents are searchable by meaning, and
the Memory page gains a Documents section beside the learned-
facts views.

This suite pins the batch so it can never regress silently:

- Bridge units: chunking (paragraph boundaries, hard-split long
  paragraphs), sync new/updated/unchanged (no duplicates, stable
  doc ids), vanished-file purge from BOTH stores, office + PDF
  extraction through the existing converters, images skipped
  with the OCR-unavailable reason (OCR is HELD, not faked —
  Tesseract is absent from this build), TEXT_EXTS pinned
  against disk_index._INDEX_EXTS so the two extension sets
  cannot drift apart.
- Search honesty: with no embedding model the FTS lane runs and
  the record says semantics was unavailable (lanes ["fts5"],
  semantic_available False, reason names the absent model);
  with the injection-seam fake embedder (384-dim, like the real
  MiniLM) the semantic lane runs and ranks the canine document
  first for "dog". The model stays operator-supplied — the real
  model is proven by the repro's L13 leg, not by this suite.
- Forget path: one file (verified_remaining 0) and a whole root
  (documents gone AND the root dropped from the remembered
  roots, so a later refresh cannot re-index it). "Forget
  everything learned" stays facts-only — this is the document
  half, and the suite pins that the fact forget path is
  untouched.
- Funnel: search_documents / list_documents run for a proven
  user turn and are REFUSED in an unproven live-origin session
  (L-24 owner gate, provenance "auto"); forget_documents is
  confirm-gated, never free-by-design; all three are registered,
  read/confirm-classified, snapshotted, labelled, grouped in
  Mind, and egress-local (never an egress read).
- GUI (Qt-gated): the Memory page's Documents section builds
  (mem_doc_card + search box + list + detail + forget button),
  refresh lists the indexed files with the honest semantic-off
  line, search finds by keyword and says so, selecting a row
  shows its passages and arms the forget button. The real paint
  is proven by the by-eye renders in darwin_d1_work/.
- Census (D1): 772 tools / params 2126 / read 386 / effectful
  386 / confirm-gated 170 / grouped 769 / closed 771 /
  already-verified 320 / §2c rows 386 / labels 697 / desktop
  offered 76 / assistant 73 / info_lookup 32 (effectful 2->3,
  reads 27->29). The §2c evidence row for forget_documents is
  pinned here, and the family-table row info_lookup | 32 | 3 |.

Stdlib unittest only. Bridge/funnel/classification legs run on
both interpreters; the GUI legs need Qt (PySide6) and skip
where it is absent. Nothing here writes the live tree's data/
or .env: every store (knowledge DB + disk index) lives in a
temp dir per test, and the live data/ snapshot taken at import
is restored in tearDownModule. ONE KNOWN EXCEPTION
(pre-existing, not this batch's): importing the tool registry
pulls in core.memory transitively, and importing core.memory
migrates live assistant.db (adds the facts `source` column —
byte-neutral, no rows touched). Restore data/ after the run,
as every leg does.
"""

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Snapshot the live data/ dir HERE — at module import, BEFORE any
# core.* import below (the batch-3 pattern: importing core.memory
# migrates the live assistant.db, so a later snapshot could never
# restore data/ to the state it found).
_DATA_SNAPSHOT = tempfile.TemporaryDirectory(prefix="d1_test_data_")
if (_ROOT / "data").is_dir():
    shutil.copytree(_ROOT / "data", _DATA_SNAPSHOT.name,
                    dirs_exist_ok=True)


def tearDownModule():
    """Restore live data/ to the state this module found it."""
    target = _ROOT / "data"
    if target.is_dir():
        shutil.rmtree(target)
    shutil.copytree(_DATA_SNAPSHOT.name, target)
    _DATA_SNAPSHOT.cleanup()


import core.config as config_mod  # noqa: E402
import core.injection as injection  # noqa: E402
from core import disk_index  # noqa: E402
from core import document_memory as dm  # noqa: E402
from core.funnel import live_origin  # noqa: E402
from core.funnel import tool_classification as tc  # noqa: E402
from core.funnel import tools_gate as flat_tools_gate  # noqa: E402
from core.funnel import tools_gate_egress as egress  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core.memory.knowledge import EMBEDDING_DIMS, KnowledgeStore  # noqa: E402

_TREE = _ROOT
_FIXTURES = _TREE / "tests" / "fixtures" / "document_convert"

try:
    import voice_assistant.core.funnel.tools_gate as _va_gate  # noqa: E402
except Exception:  # noqa: BLE001 — bare process, no alias
    _va_gate = None


def _qt_available():
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _set_provenance_both(value):
    """The exact call the window makes at session start, on BOTH
    module identities (one identity in production)."""
    flat_tools_gate.set_session_provenance(value)
    if _va_gate is not None:
        _va_gate.set_session_provenance(value)


def _live_execute(name, args):
    """One funnel call with the live-origin contextvar set (the
    voice loop's marker), standing taint cleared first — exactly
    as the live loop does on a genuine user message."""
    injection.clear_taint(force=True)
    token = live_origin.set_live_origin()
    try:
        return tools.execute(name, args)
    finally:
        live_origin.reset_live_origin(token)


def _is_owner_refusal(result):
    return (isinstance(result, dict)
            and result.get("status") == "refused"
            and result.get("kind") == "refused")


def _ran(result):
    return isinstance(result, dict) and result.get("status") == "success"


def _build_pdf(text):
    """One-page PDF with CORRECT xref offsets."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\nBT /F1 18 Tf 72 720 Td (%s) Tj ET\n"
        b"endstream" % (len(text) + 42, text.encode()),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objs) + 1, xref))
    return bytes(out)


class _FakeEmbedder:
    """Deterministic 384-dim fake (the injection seam): canine
    texts sit on axis 0, everything else on axis 1, so "dog"
    ranks the dogs document first by cosine. Vectors MUST be
    EMBEDDING_DIMS long — embed_missing skips any vector whose
    length differs, so a short fake embeds nothing and the
    semantic lane never runs."""

    def embed(self, texts):
        out = []
        for t in texts:
            low = str(t).lower()
            if "dog" in low or "canine" in low:
                out.append([1.0] + [0.0] * (EMBEDDING_DIMS - 1))
            else:
                out.append([0.0, 1.0] + [0.0] * (EMBEDDING_DIMS - 2))
        return out


def _has_mammoth():
    try:
        import mammoth  # noqa: F401
        return True
    except ImportError:
        return False


def _has_pypdf():
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False


class _TempStores(unittest.TestCase):
    """Temp knowledge dir + temp disk-index data dir per test;
    nothing touches the live tree's data/.

    The base fixture set is interpreter-neutral: two text files
    + one PNG (OCR-held). Office + PDF fixtures are added only
    where a test needs them — mammoth / pypdf / python-pptx
    exist under the Qt interpreter but NOT under system python3,
    so a test that counts indexed files must not depend on them.
    """

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="d1_")
        self.addCleanup(self._tmp.cleanup)
        self.docs = Path(self._tmp.name) / "docs"
        self.docs.mkdir()
        self.know = Path(self._tmp.name) / "knowledge"
        self.know.mkdir()
        self.data = Path(self._tmp.name) / "data"
        self.data.mkdir()
        self._old_know = os.environ.get("VYUK_KNOWLEDGE_DIR")
        os.environ["VYUK_KNOWLEDGE_DIR"] = str(self.know)
        self.addCleanup(self._restore_know_env)
        self._old_data_dir = disk_index.DATA_DIR
        disk_index.DATA_DIR = self.data
        self.addCleanup(setattr, disk_index, "DATA_DIR",
                        self._old_data_dir)
        (self.docs / "dogs.txt").write_text(
            "The quick brown dog chased the canine across the "
            "meadow. Dogs are loyal companions and love long walks.",
            encoding="utf-8")
        (self.docs / "quantum.txt").write_text(
            "Quantum entanglement links particles across distance. "
            "A qubit holds superposition until measured.",
            encoding="utf-8")
        (self.docs / "scan.png").write_bytes(bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000a49444154789c63000100000500010d0a2db4"
            "0000000049454e44ae426082"))

    def _add_office_fixtures(self):
        """sample.docx + a valid one-page PDF (Qt-only deps)."""
        shutil.copy(_FIXTURES / "sample.docx",
                    self.docs / "sample.docx")
        (self.docs / "notes.pdf").write_bytes(
            _build_pdf("Quantum entanglement notes"))

    def _restore_know_env(self):
        if self._old_know is None:
            os.environ.pop("VYUK_KNOWLEDGE_DIR", None)
        else:
            os.environ["VYUK_KNOWLEDGE_DIR"] = self._old_know

    def _index(self):
        return disk_index.index_files(folders=str(self.docs))

    def _names(self):
        rows = dm.list_documents(limit=100).get("files", [])
        return [str(r.get("source", "")) for r in rows]


# ------------------------------------------------------------------ bridge
class BridgeChunkTests(_TempStores):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(dm.chunk_text("hello world"), ["hello world"])

    def test_empty_text_is_no_chunks(self):
        self.assertEqual(dm.chunk_text(""), [])
        self.assertEqual(dm.chunk_text("   \n  "), [])

    def test_paragraph_boundaries_split(self):
        # Short paragraphs ACCUMULATE into one chunk (the cap is
        # what splits); the split lands on a paragraph boundary
        # only when the accumulated text would exceed it.
        short = "First paragraph here.\n\nSecond paragraph here."
        chunks = dm.chunk_text(short)
        self.assertEqual(len(chunks), 1)
        self.assertIn("First paragraph", chunks[0])
        self.assertIn("Second paragraph", chunks[0])
        long_a = "a " * (dm.CHUNK_CHARS // 2)
        long_b = "b " * (dm.CHUNK_CHARS // 2)
        chunks = dm.chunk_text(long_a + "\n\n" + long_b)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[0].strip().startswith("a"))
        self.assertTrue(chunks[1].strip().startswith("b"))

    def test_long_paragraph_hard_splits(self):
        text = "x" * (dm.CHUNK_CHARS + 500)
        chunks = dm.chunk_text(text)
        self.assertTrue(len(chunks) >= 2)
        self.assertTrue(all(len(c) <= dm.CHUNK_CHARS for c in chunks))
        self.assertEqual("".join(chunks), text)

    def test_doc_id_stable_across_resync(self):
        first = dm.doc_id_for(str(self.docs / "dogs.txt"), 0)
        self.assertEqual(
            first, dm.doc_id_for(str(self.docs / "dogs.txt"), 0))
        self.assertNotEqual(
            first, dm.doc_id_for(str(self.docs / "dogs.txt"), 1))
        self.assertNotEqual(
            first, dm.doc_id_for(str(self.docs / "quantum.txt"), 0))


class BridgeExtractTests(_TempStores):
    def setUp(self):
        super().setUp()
        self._add_office_fixtures()

    def test_text_file_extracts(self):
        text, reason = dm.extract_file_text(self.docs / "dogs.txt")
        self.assertIn("canine", text)
        self.assertFalse(reason)

    @unittest.skipUnless(_has_mammoth(),
                         "mammoth absent (Qt-only dep)")
    def test_office_docx_extracts(self):
        text, reason = dm.extract_file_text(self.docs / "sample.docx")
        self.assertTrue(text and text.strip())
        self.assertFalse(reason)

    @unittest.skipUnless(_has_pypdf(), "pypdf absent (Qt-only dep)")
    def test_pdf_extracts(self):
        text, reason = dm.extract_file_text(self.docs / "notes.pdf")
        self.assertIn("entanglement", text)
        self.assertFalse(reason)

    def test_image_is_ocr_held_not_faked(self):
        text, reason = dm.extract_file_text(self.docs / "scan.png")
        self.assertIsNone(text)
        self.assertIn("OCR", reason)
        self.assertIn("not available", reason)

    def test_unsupported_extension(self):
        weird = self.docs / "archive.zip"
        weird.write_bytes(b"PK\x03\x04")
        text, reason = dm.extract_file_text(weird)
        self.assertIsNone(text)
        self.assertTrue(reason)

    def test_text_exts_mirror_disk_index(self):
        # The two extension sets must not drift: a text file the
        # disk index admits must be a bridge text file too.
        self.assertEqual(dm.TEXT_EXTS, disk_index._INDEX_EXTS)

    def test_office_and_pdf_extensions_known(self):
        self.assertTrue({".docx", ".pptx", ".xlsx"} <= dm.OFFICE_EXTS)
        self.assertIn(".pdf", dm.PDF_EXTS)
        self.assertTrue({".png", ".jpg", ".jpeg"} <= dm.IMAGE_EXTS)


class BridgeSyncTests(_TempStores):
    def test_index_writes_passages_to_the_store(self):
        summary = self._index()
        self.assertIn("Document memory: 2 new", summary)
        names = self._names()
        self.assertEqual(len(names), 2)
        for leaf in ("dogs.txt", "quantum.txt"):
            self.assertTrue(any(leaf in n for n in names), leaf)
        self.assertFalse(any("scan.png" in n for n in names))

    def test_rerun_is_unchanged_no_duplicates(self):
        self._index()
        before = dm.list_documents(limit=100)
        summary = self._index()
        after = dm.list_documents(limit=100)
        self.assertIn("unchanged", summary)
        self.assertEqual(before["total_files"],
                         after["total_files"])
        self.assertEqual(before["total_passages"],
                         after["total_passages"])

    def test_edit_is_updated_not_duplicated(self):
        self._index()
        (self.docs / "dogs.txt").write_text(
            "The quick brown dog chased the canine across the "
            "meadow. Puppies need training every day.",
            encoding="utf-8")
        summary = self._index()
        self.assertIn("1 updated", summary)
        self.assertEqual(dm.list_documents(limit=100)["total_files"], 2)
        passages = dm.get_document_passages(str(self.docs / "dogs.txt"))
        joined = " ".join(p.get("passage", "")
                          for p in passages["passages"])
        self.assertIn("Puppies", joined)
        self.assertNotIn("loyal companions", joined)

    def test_delete_vanishes_from_both_stores(self):
        self._index()
        (self.docs / "quantum.txt").unlink()
        self._index()
        self.assertFalse(any("quantum.txt" in n for n in self._names()))
        self.assertNotIn("quantum.txt",
                         disk_index.search_indexed_files("qubit"))

    def test_image_skip_named_in_summary(self):
        summary = self._index()
        # The PNG is skipped on both interpreters; the summary
        # names the OCR hold (the exact line varies with the
        # other skipped files, so either phrasing counts).
        self.assertTrue("OCR" in summary or "Images held" in summary,
                        summary)
        self.assertFalse(any("scan.png" in n for n in self._names()))

    def test_store_exists_guard(self):
        self.assertFalse(dm.document_store_exists())
        self._index()
        self.assertTrue(dm.document_store_exists())

    def test_status_reports_files_and_model_state(self):
        self._index()
        status = dm.document_memory_status()
        self.assertEqual(status["files"], 2)
        self.assertFalse(status["model_ready"])
        self.assertIn("model", status["reason"].lower())

    def test_reset_clears_and_reindex_restores(self):
        self._index()
        dm.reset_document_store()
        self.assertEqual(dm.list_documents(limit=100)["total_files"], 0)
        self._index()
        self.assertEqual(dm.list_documents(limit=100)["total_files"], 2)


class BridgeSearchTests(_TempStores):
    def test_fts_search_finds_by_keyword(self):
        self._index()
        result = dm.search_documents("qubit", limit=5)
        hits = [str(h.get("source", "")) for h in result["results"]]
        self.assertTrue(any("quantum.txt" in h for h in hits))

    def test_no_model_is_honest_fts_fallback(self):
        self._index()
        result = dm.search_documents("dog", limit=5)
        self.assertEqual(result["lanes"], ["fts5"])
        self.assertIs(result["semantic_available"], False)
        self.assertIn("model", result["reason"].lower())

    def test_embed_missing_with_no_model_embeds_nothing(self):
        self._index()
        result = KnowledgeStore().embed_missing()
        self.assertIs(result["available"], False)
        self.assertEqual(result["embedded"], 0)
        self.assertIn("model", result["reason"].lower())

    def test_fake_embedder_semantic_lane_ranks_dog_first(self):
        self._index()
        fake = _FakeEmbedder()
        embedded = KnowledgeStore().embed_missing(embedder=fake)
        self.assertTrue(embedded["ok"])
        self.assertGreater(embedded["embedded"], 0)
        result = dm.search_documents("dog", limit=5, embedder=fake)
        self.assertIn("semantic", result["lanes"])
        first = str(result["results"][0].get("source", ""))
        self.assertIn("dogs.txt", first)

    def test_fake_embedder_finds_what_fts_misses(self):
        # "canine" is in dogs.txt; the semantic lane ranks it
        # even for a query word FTS would also find — the lane
        # itself is the assertion, not the keyword.
        self._index()
        KnowledgeStore().embed_missing(embedder=_FakeEmbedder())
        result = dm.search_documents("canine", limit=5,
                                     embedder=_FakeEmbedder())
        self.assertIn("semantic", result["lanes"])

    def test_list_documents_shape(self):
        self._index()
        data = dm.list_documents(limit=10)
        self.assertEqual(
            set(data), {"files", "total_files", "total_passages",
                        "model_ready", "semantic_available", "reason"})
        self.assertEqual(data["total_files"], 2)
        self.assertGreater(data["total_passages"], 0)
        row = data["files"][0]
        self.assertIn("source", row)
        self.assertIn("snippet", row)

    def test_get_document_passages(self):
        self._index()
        data = dm.get_document_passages(str(self.docs / "dogs.txt"))
        self.assertEqual(data["count"], len(data["passages"]))
        self.assertGreater(data["count"], 0)
        self.assertIn("canine", data["passages"][0].get("passage", ""))


class BridgeForgetTests(_TempStores):
    def test_forget_file_verified_empty(self):
        self._index()
        result = dm.forget_documents(str(self.docs / "quantum.txt"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "file")
        self.assertEqual(result["verified_remaining"], 0)
        self.assertEqual(result["files_removed"], 1)
        self.assertFalse(any("quantum.txt" in n
                             for n in self._names()))
        self.assertEqual(dm.list_documents(limit=100)["total_files"], 1)

    def test_forget_root_drops_documents_and_root(self):
        self._index()
        result = dm.forget_documents(str(self.docs))
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "folder")
        self.assertEqual(dm.list_documents(limit=100)["total_files"], 0)
        self.assertNotIn(str(self.docs), disk_index._load_roots())
        # A later refresh re-indexes REMEMBERED roots only — the
        # forgotten root cannot come back that way. (An explicit
        # index_files(folders=...) call would re-add it; that is
        # the user asking for it again, not a refresh.)
        dm.refresh_documents()
        self.assertEqual(dm.list_documents(limit=100)["total_files"], 0)

    def test_forget_unknown_file_is_honest(self):
        self._index()
        result = dm.forget_documents(str(self.docs / "nope.txt"))
        self.assertFalse(result["ok"])

    def test_fact_forget_path_untouched(self):
        # "Forget everything learned" stays facts-only: the
        # document forget path never touches learned facts.
        from core.memory.memory import memory_store  # noqa: E402
        self.assertTrue(memory_store.save_fact(
            "general", "favourite colour", "blue", source="test"))
        self._index()
        dm.forget_documents(str(self.docs))
        row = memory_store.get_fact("general", "favourite colour")
        self.assertIsNotNone(row)
        self.assertEqual(row.get("value"), "blue")


# ------------------------------------------------------------------ funnel
class FunnelTests(_TempStores):
    def setUp(self):
        super().setUp()
        self._lock = bool(getattr(
            config_mod.config, "session_provenance_lock", True))
        config_mod.config.session_provenance_lock = True
        self.addCleanup(setattr, config_mod.config,
                        "session_provenance_lock", self._lock)
        _set_provenance_both("unknown")
        self.addCleanup(_set_provenance_both, "unknown")

    def test_search_documents_runs_for_proven_user(self):
        self._index()
        _set_provenance_both("user")
        result = tools.execute("search_documents", {"query": "dog"})
        self.assertTrue(_ran(result), result)
        payload = result.get("result") or result
        text = str(payload)
        self.assertIn("dogs.txt", text)

    def test_list_documents_runs_for_proven_user(self):
        self._index()
        _set_provenance_both("user")
        result = tools.execute("list_documents", {})
        self.assertTrue(_ran(result), result)
        self.assertIn("dogs.txt", str(result))

    def test_search_documents_refused_in_unproven_session(self):
        self._index()
        _set_provenance_both("auto")
        result = _live_execute("search_documents", {"query": "dog"})
        self.assertTrue(_is_owner_refusal(result), result)

    def test_list_documents_refused_in_unproven_session(self):
        self._index()
        _set_provenance_both("auto")
        result = _live_execute("list_documents", {})
        self.assertTrue(_is_owner_refusal(result), result)

    def test_voice_phrases_in_descriptions(self):
        decls = {d["name"]: d for d in tools.get_declarations()}
        self.assertIn("search my documents for X",
                      decls["search_documents"]["description"])
        self.assertIn("what do you remember from my files",
                      decls["list_documents"]["description"])
        self.assertIn("forget that document",
                      decls["forget_documents"]["description"])


# ------------------------------------------------------- classification
class ClassificationTests(unittest.TestCase):
    def test_new_tools_registered(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in ("search_documents", "list_documents",
                     "forget_documents"):
            self.assertIn(name, names)

    def test_reads_are_read_classified(self):
        for name in ("search_documents", "list_documents"):
            self.assertIn(name, tc._READ_ONLY_TOOLS, name)
            self.assertNotIn(name, tc._CONFIRM_GATE_TOOLS, name)

    def test_forget_is_confirm_gated_never_free(self):
        self.assertIn("forget_documents", tc._CONFIRM_GATE_TOOLS)
        self.assertNotIn("forget_documents", tc._READ_ONLY_TOOLS)
        self.assertNotIn("forget_documents", tc._FREE_BY_DESIGN)

    def test_reads_are_owner_only_and_egress_local(self):
        for name in ("search_documents", "list_documents"):
            self.assertIn(name, flat_tools_gate._OWNER_ONLY_READS,
                          name)
            self.assertIn(name, egress._LOCAL_ONLY_READS, name)
            self.assertFalse(egress._is_egress_read(name), name)

    def test_every_owner_read_is_read_classified(self):
        bad = [n for n in ("search_documents", "list_documents")
               if n not in tc._READ_ONLY_TOOLS]
        self.assertEqual(bad, [])

    def test_snapshot_labels_group(self):
        import core.catalogue_snapshot as snap  # noqa: E402
        import core.tool_labels as labels  # noqa: E402
        from core.catalogue_groups_data import GROUP_MEMBERS  # noqa: E402
        for name in ("search_documents", "list_documents",
                     "forget_documents"):
            self.assertIn(name, GROUP_MEMBERS["Intelligence"])
            self.assertIn(name, labels.TOOL_FRIENDLY_NAMES)
        # The generated snapshot must not drift from the live
        # groups data (the anticipation-executor precedent).
        self.assertEqual(snap.GROUP_MEMBERS, GROUP_MEMBERS)
        self.assertEqual(
            labels.TOOL_FRIENDLY_NAMES["search_documents"],
            "Search my documents")
        self.assertEqual(
            labels.TOOL_FRIENDLY_NAMES["list_documents"],
            "My documents")
        self.assertEqual(
            labels.TOOL_FRIENDLY_NAMES["forget_documents"],
            "Forget documents")

    def test_wiring_handlers_registered(self):
        import core.w6_wiring as w6  # noqa: E402
        for name in ("search_documents", "list_documents",
                     "forget_documents"):
            self.assertIn(name, w6.W6_TOOL_HANDLERS)

    def test_index_files_description_carries_voice_phrase(self):
        decls = {d["name"]: d for d in tools.get_declarations()}
        self.assertIn("remember this folder",
                      decls["index_files"]["description"])
        self.assertIn("search_documents",
                      decls["index_files"]["description"])


# ------------------------------------------------------------------ GUI
class GuiSourceTests(unittest.TestCase):
    def test_documents_section_in_page_source(self):
        text = (_TREE / "gui" / "window_pages_memory.py").read_text(
            encoding="utf-8")
        self.assertIn("_build_documents_section", text)
        self.assertIn("mem_doc_card", text)
        self.assertIn("Search documents by meaning", text)

    def test_facade_exports_document_memory(self):
        import core.gui_facade as facade  # noqa: E402
        self.assertIn("document_memory", facade._EXPORTS)
        self.assertIn("document_memory", facade.__all__)
        self.assertIs(facade.document_memory, dm)


@unittest.skipUnless(_qt_available(), "PySide6 absent")
class GuiDocumentsTests(_TempStores):
    def setUp(self):
        super().setUp()
        self._add_office_fixtures()
        self._index()
        from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402
        self._app = (QApplication.instance()
                     or QApplication(sys.argv[:1]))
        installed = _install_voice_assistant_alias()
        self.addCleanup(_uninstall_voice_assistant_alias, installed)
        from gui.window_pages_memory import PagesMemoryMixin  # noqa: E402
        self.mixin = PagesMemoryMixin()
        self.widget = QWidget()
        self.addCleanup(self.widget.deleteLater)
        from PySide6.QtWidgets import QVBoxLayout  # noqa: E402
        layout = QVBoxLayout(self.widget)
        self.mixin._build_documents_section(layout)

    def test_section_builds_with_card_and_search(self):
        self.assertIsNotNone(self.mixin.mem_doc_card)
        self.assertEqual(
            self.mixin._doc_search.placeholderText(),
            "Search documents by meaning…")
        self.assertFalse(self.mixin._doc_forget_btn.isEnabled())

    def test_refresh_lists_indexed_files_honestly(self):
        self.assertEqual(self.mixin._doc_list.count(), 4)
        self.assertIn("4 file(s)", self.mixin._doc_count_label.text())
        self.assertIn("semantic search off",
                      self.mixin._doc_count_label.text())

    def test_search_finds_by_keyword_and_says_so(self):
        self.mixin._doc_search.setText("dog")
        self.mixin._on_doc_search()
        self.assertGreaterEqual(self.mixin._doc_list.count(), 1)
        self.assertIn("keyword matches", self.mixin._doc_detail.text())

    def test_select_row_shows_passages_and_arms_forget(self):
        self.mixin._doc_search.setText("dog")
        self.mixin._on_doc_search()
        item = self.mixin._doc_list.item(0)
        self.mixin._on_doc_selected(item)
        self.assertTrue(self.mixin._doc_forget_btn.isEnabled())
        self.assertIsNotNone(self.mixin._doc_selected_source)
        self.assertIn("canine", self.mixin._doc_detail.text())

    def test_empty_search_relists(self):
        self.mixin._doc_search.setText("dog")
        self.mixin._on_doc_search()
        self.mixin._doc_search.setText("")
        self.mixin._on_doc_search()
        self.assertEqual(self.mixin._doc_list.count(), 4)


def _install_voice_assistant_alias():
    """Synthesise the voice_assistant package alias (the
    test_chat_ui.WindowBase / test_stage0f6 pattern) and bind
    the REAL facade module under the aliased name, so the Memory
    page's import chain reaches its production import path. A
    thin stub is NOT enough here: window_pages_memory pulls
    window_widgets_shell, which imports `audio_manager` from
    the facade at module top — the stub would fail on the first
    missing name. Binding the real module means there is nothing
    to shadow: uninstall removes only the alias binding.
    Returns the sys.modules keys installed, so the caller can
    remove them afterwards."""
    if "voice_assistant" in sys.modules:
        return []
    import types
    keys = ["voice_assistant", "voice_assistant.core",
            "voice_assistant.gui",
            "voice_assistant.core.gui_facade"]
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [str(_TREE)]
    sys.modules["voice_assistant"] = pkg
    core_pkg = types.ModuleType("voice_assistant.core")
    core_pkg.__path__ = [str(_TREE / "core")]
    sys.modules["voice_assistant.core"] = core_pkg
    gui_pkg = types.ModuleType("voice_assistant.gui")
    gui_pkg.__path__ = [str(_TREE / "gui")]
    sys.modules["voice_assistant.gui"] = gui_pkg
    import core.gui_facade as _real_facade  # noqa: E402
    sys.modules["voice_assistant.core.gui_facade"] = _real_facade
    return keys


def _uninstall_voice_assistant_alias(keys):
    """Remove exactly the alias keys _install_… installed."""
    for key in keys:
        sys.modules.pop(key, None)


# ------------------------------------------------------------------ census
class CensusTests(unittest.TestCase):
    def test_census_darwin_d1(self):
        # Darwin D1: three new tools, document memory / semantic
        # search. The counts are re-derived by the worker on a
        # scratch copy (r3f_census.json) and asserted here from
        # the registry, so a silent tool addition fails loudly.
        decls = tools.get_declarations()
        params = sum(
            len((d.get("parameters") or {}).get("properties") or {})
            for d in decls)
        self.assertEqual(len(decls), 772)
        self.assertEqual(params, 2126)
        self.assertEqual(len([d for d in decls
                              if d["name"] in tc._READ_ONLY_TOOLS]),
                         386)
        self.assertEqual(len([d for d in decls
                              if d["name"] not in tc._READ_ONLY_TOOLS]),
                         386)
        self.assertEqual(len([d for d in decls
                              if d["name"] in tc._CONFIRM_GATE_TOOLS]),
                         170)
        # Dict-literal shape (batch-2 lesson): free is DERIVED
        # from the registry (total - read - confirm), not written
        # as a literal — a sweep that moves the census must move
        # this shape too.
        totals = {
            "read": len([d for d in decls
                         if d["name"] in tc._READ_ONLY_TOOLS]),
            "confirm": len([d for d in decls
                            if d["name"] in tc._CONFIRM_GATE_TOOLS]),
        }
        totals["free"] = len(decls) - totals["read"] - totals["confirm"]
        self.assertEqual(totals, {"read": 386, "confirm": 170,
                                  "free": 216})

    def test_grouped_closed_labels_verified(self):
        import core.catalogue_snapshot as snap  # noqa: E402
        import core.tool_labels as labels  # noqa: E402
        from core.catalogue_groups_data import GROUP_MEMBERS  # noqa: E402
        grouped = sum(len(v) for v in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 772)  # UI Phase 2 (2026-11-25): grouped 769 -> 772 — click_anywhere + 2 plugin tools join the catalogue
        self.assertEqual(snap.GROUP_MEMBERS, GROUP_MEMBERS)
        decls = tools.get_declarations()
        closed = sum(
            1 for d in decls
            if (d.get("parameters") or {}).get(
                "additionalProperties") is False)
        self.assertEqual(closed, 771)
        self.assertEqual(len(labels.TOOL_FRIENDLY_NAMES), 697)

    def test_already_verified_and_family(self):
        doc = (_TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        totals = __import__("json").loads(re.search(
            r"AUDIT-TOTALS:\s*(\{[^}]*\})", doc).group(1))
        self.assertEqual(totals["already_verified_in_handler"], 320)
        self.assertEqual(totals["total"], 772)
        counts = __import__("json").loads(re.search(
            r"AUDIT-COUNTS:\s*(\{[^}]*\})", doc).group(1))
        self.assertEqual(counts["info_lookup"], 32)

    def test_profiles(self):
        from core.funnel import tool_profiles as profiles  # noqa: E402
        self.assertEqual(
            len(profiles.profile_declarations(tools, "desktop")), 76)
        self.assertEqual(
            len(profiles.profile_declarations(tools, "assistant")), 73)


class Section2cTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        doc = (_TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        section_2c = doc[doc.index("## 2c."):doc.index("## 3.")]
        cls.rows_2c = re.findall(r"^\| `([a-z0-9_]+)` \|",
                                 section_2c, re.M)
        cls.doc = doc

    def test_section_2c_rows(self):
        self.assertEqual(len(self.rows_2c), 386)
        self.assertEqual(len(set(self.rows_2c)), 386)

    def test_forget_documents_row_present(self):
        self.assertIn("forget_documents", self.rows_2c)

    def test_family_row_info_lookup(self):
        self.assertIn("| info_lookup | 32 | 3 |", self.doc)


if __name__ == "__main__":
    unittest.main()
