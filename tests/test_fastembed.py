"""R8-1 fastembed (PORT, not adopt) — KnowledgeStore vector index.

Tests the port onto the tree's own MiniLM producer: the embeddings
table fills, semantic search ranks by cosine over it, hybrid search
runs a real semantic lane, and every model-absent path is honest.

The fake embedder encodes synonymy by hand (dog/canine share a
coordinate, garden/yard share one) so the tests assert the
SEMANTIC structure, not the real model's behaviour. The real model
is covered by test_real_onnx_miniLM, which runs only on Qt with the
scratch model present (~/workspace/code-lab/fastembed_scratch/,
downloaded in the build, sha256-pinned to the manifest) and skips
everywhere else — a skip, never a pass on a stub.

Stdlib unittest only. Stores live in temp dirs; nothing touches
the tree's data/.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest

from core.memory import knowledge as kn

# Synonym groups the fake embedder understands: words in one group
# share a coordinate, so a query matches its paraphrase.
_GROUPS = (
    ("dog", "canine", "puppy", "pet"),
    ("ball", "toy"),
    ("garden", "yard", "outside"),
    ("quantum", "entanglement", "physics"),
    ("bread", "sourdough", "baking"),
)


class _FakeEmbedder:
    """Deterministic 384-dim vectors: 1.0 at each synonym-group
    coordinate the text touches, L2-normalised. No model, no
    network — the semantic structure is the test's own fixture."""

    def embed(self, texts):
        out = []
        for text in texts:
            vec = [0.0] * kn.EMBEDDING_DIMS
            lowered = (text or "").lower()
            for idx, group in enumerate(_GROUPS):
                if any(word in lowered for word in group):
                    vec[idx] = 1.0
            norm = sum(x * x for x in vec) ** 0.5 or 1.0
            out.append([x / norm for x in vec])
        return out


class _WrongDimsEmbedder:
    """Returns 128-dim vectors — the store must skip those docs,
    never crash, never store a short vector."""

    def embed(self, texts):
        return [[0.1] * 128 for _ in texts]


class _FailingEmbedder:
    """Raises on embed — the store must report the failure, never
    raise into a caller."""

    def embed(self, texts):
        raise RuntimeError("boom")


DOCS = (
    ("doc-dog", "Dog in the garden",
     "The dog chased the ball across the garden"),
    ("doc-canine", "Canine in the yard",
     "A canine ran after the toy in the yard"),
    ("doc-quantum", "Quantum notes",
     "Quantum entanglement violates local realism"),
    ("doc-bread", "Bread notes",
     "How to bake sourdough bread at home"),
)


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = kn.KnowledgeStore(
            db_path=os.path.join(self._tmp.name, "k.db"))
        for doc_id, title, passage in DOCS:
            self.store.add_document(doc_id, title, passage)
        self.emb = _FakeEmbedder()

    def tearDown(self):
        self._tmp.cleanup()

    # -- embed_missing -------------------------------------------------

    def test_embed_missing_fills_table_and_re_reads(self):
        result = self.store.embed_missing(embedder=self.emb)
        self.assertTrue(result["ok"])
        self.assertTrue(result["available"])
        self.assertEqual(result["embedded"], 4)
        self.assertEqual(result["model"], "minilm-l6-v2")
        self.assertEqual(result["dims"], 384)
        # A4-filestat: the verified count comes from the table.
        self.assertEqual(result["verified_embedded"], 4)
        self.assertEqual(result["already_embedded"], 0)
        status = self.store.vector_index_status()
        self.assertEqual(status["embedded"], 4)
        self.assertEqual(status["total"], 4)
        self.assertEqual(status["unembedded"], 0)

    def test_embed_missing_idempotent(self):
        self.store.embed_missing(embedder=self.emb)
        second = self.store.embed_missing(embedder=self.emb)
        self.assertEqual(second["embedded"], 0)
        self.assertEqual(second["already_embedded"], 4)

    def test_embed_missing_no_model_is_honest(self):
        result = self.store.embed_missing(embedder=None)
        # No model in the lab tree's data/embeddings/ — and the
        # test never downloads one. Nothing is fabricated.
        self.assertFalse(result["available"])
        self.assertEqual(result["embedded"], 0)
        self.assertIn("absent", result["reason"])
        status = self.store.vector_index_status()
        self.assertFalse(status["model_ready"])
        self.assertFalse(status["semantic_available"])
        self.assertEqual(status["embedded"], 0)

    def test_embed_missing_wrong_dims_skips_docs(self):
        result = self.store.embed_missing(embedder=_WrongDimsEmbedder())
        self.assertTrue(result["available"])
        self.assertEqual(result["embedded"], 0)
        status = self.store.vector_index_status()
        self.assertEqual(status["embedded"], 0)

    def test_embed_missing_failing_embedder_reports(self):
        result = self.store.embed_missing(embedder=_FailingEmbedder())
        self.assertFalse(result["ok"])
        self.assertFalse(result["available"])
        self.assertIn("boom", result["reason"])

    # -- semantic_search ------------------------------------------------

    def test_semantic_search_cosine_order(self):
        self.store.embed_missing(embedder=self.emb)
        result = self.store.semantic_search("a pet dog playing outside",
                                            embedder=self.emb)
        self.assertTrue(result["available"])
        self.assertEqual(result["model"], "minilm-l6-v2")
        self.assertEqual(result["dims"], 384)
        self.assertEqual(result["vectors"], 4)
        names = [r["doc_id"] for r in result["results"]]
        # The paraphrase (canine/yard) ranks with the literal dog
        # doc; the quantum and bread docs score zero and are
        # excluded by the SEMANTIC_MIN_COSINE threshold. The two
        # dog docs tie on the fake vectors, so their internal
        # order is free.
        self.assertEqual(set(names[:2]), {"doc-dog", "doc-canine"})
        self.assertEqual(names[2:], [])
        scores = [r["score"] for r in result["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(-1.0 <= s <= 1.0 for s in scores))

    def test_semantic_search_no_vectors_is_honest(self):
        result = self.store.semantic_search("dog", embedder=self.emb)
        self.assertFalse(result["available"])
        self.assertEqual(result["results"], [])
        self.assertIn("embed_knowledge", result["reason"])

    def test_semantic_search_no_model_is_honest(self):
        self.store.embed_missing(embedder=self.emb)
        result = self.store.semantic_search("dog", embedder=None)
        self.assertFalse(result["available"])
        self.assertEqual(result["results"], [])
        self.assertIn("absent", result["reason"])

    def test_semantic_search_empty_query(self):
        result = self.store.semantic_search("   ", embedder=self.emb)
        self.assertFalse(result["available"])
        self.assertEqual(result["results"], [])

    def test_corrupt_blob_skipped_not_fatal(self):
        self.store.embed_missing(embedder=self.emb)
        conn = self.store._connect()
        try:
            conn.execute(
                "UPDATE embeddings SET vector = ? WHERE doc_rowid = 1",
                (b"not-a-vector",))
            conn.commit()
        finally:
            conn.close()
        result = self.store.semantic_search("dog", embedder=self.emb)
        self.assertTrue(result["available"])
        # 3 valid vectors remain (the corrupt row is skipped, not
        # counted); only the canine doc scores above the threshold
        # for a "dog" query.
        self.assertEqual(result["vectors"], 3)
        self.assertEqual([r["doc_id"] for r in result["results"]],
                         ["doc-canine"])

    # -- hybrid search ---------------------------------------------------

    def test_search_fts5_miss_semantic_catch(self):
        """The discrimination test: 'dog garden' shares NO word
        with the canine doc, so FTS5 misses it; semantics catches
        it (dog~canine, garden~yard)."""
        self.store.embed_missing(embedder=self.emb)
        result = self.store.search("dog garden", embedder=self.emb)
        self.assertIn("fts5", result["lanes"])
        self.assertIn("semantic", result["lanes"])
        names = [r["doc_id"] for r in result["results"]]
        self.assertIn("doc-dog", names)
        self.assertIn("doc-canine", names)
        self.assertNotIn("doc-quantum", names)
        self.assertTrue(all(r["semantic_score"] is not None
                            for r in result["results"]))

    def test_search_without_model_is_fts5_only(self):
        result = self.store.search("dog garden", embedder=None)
        self.assertEqual(result["lanes"], ["fts5"])
        self.assertFalse(result["semantic_available"])
        self.assertEqual([r["doc_id"] for r in result["results"]],
                         ["doc-dog"])
        self.assertTrue(all(r["semantic_score"] is None
                            for r in result["results"]))

    def test_search_never_claims_vector_lane(self):
        """The old label lie is gone: no 'vector' lane ever
        appears, with or without a model."""
        self.store.embed_missing(embedder=self.emb)
        for emb in (self.emb, None):
            result = self.store.search("dog", embedder=emb)
            self.assertNotIn("vector", result["lanes"])

    def test_search_empty_query(self):
        result = self.store.search("   ")
        self.assertEqual(result["results"], [])
        self.assertEqual(result["lanes"], [])

    # -- vector pack/unpack ------------------------------------------------

    def test_pack_unpack_round_trip(self):
        vec = [0.5, -0.25, 0.0] * 128
        self.assertEqual(kn._unpack_vector(kn._pack_vector(vec), 384), vec)
        self.assertEqual(len(kn._pack_vector(vec)), 4 * 384)
        self.assertIsNone(kn._unpack_vector(b"short", 384))
        self.assertIsNone(kn._unpack_vector(None, 384))


class _WiringCase(unittest.TestCase):
    """w6_wiring wrappers over a temp store."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._tmp.name, "k.db")
        from core import w6_wiring as w6
        self.w6 = w6
        store = kn.KnowledgeStore(db_path=self.db_path)
        for doc_id, title, passage in DOCS:
            store.add_document(doc_id, title, passage)

    def tearDown(self):
        self._tmp.cleanup()

    def test_status_reports_index(self):
        result = self.w6.knowledge_embedding_status(db_path=self.db_path)
        self.assertEqual(result["model"], "minilm-l6-v2")
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["embedded"], 0)
        self.assertFalse(result["semantic_available"])
        self.assertIn("embedder", result)

    def test_semantic_search_tool_honest_without_model(self):
        result = self.w6.knowledge_semantic_search(
            "dog", db_path=self.db_path)
        self.assertFalse(result["available"])
        self.assertEqual(result["results"], [])

    def test_embed_tool_honest_without_model(self):
        result = self.w6.knowledge_embed_missing_tool(
            {"limit": 50}, db_path=self.db_path)
        self.assertFalse(result["available"])
        self.assertEqual(result["embedded"], 0)


class _RegistryCase(unittest.TestCase):
    """The three tools are declared, gated, and grouped."""

    def test_tools_declared_with_right_schemas(self):
        from core.funnel.tools_registry import tools
        decls = {d["name"]: d for d in tools.get_declarations()}
        for name in ("semantic_search_knowledge", "embed_knowledge",
                     "knowledge_embedding_status"):
            self.assertIn(name, decls)
        props = decls["semantic_search_knowledge"]["parameters"]["properties"]
        self.assertEqual(set(props), {"query", "limit"})
        self.assertEqual(
            decls["semantic_search_knowledge"]["parameters"]["required"],
            ["query"])
        props = decls["embed_knowledge"]["parameters"]["properties"]
        self.assertEqual(set(props), {"limit"})
        self.assertEqual(
            decls["embed_knowledge"]["parameters"]["required"], [])
        props = decls["knowledge_embedding_status"]["parameters"]["properties"]
        self.assertEqual(set(props), set())
        # Closed schemas (additionalProperties False) like their
        # knowledge_search sibling.
        for name in ("semantic_search_knowledge", "embed_knowledge",
                     "knowledge_embedding_status"):
            self.assertIs(
                decls[name]["parameters"].get("additionalProperties"),
                False, name)

    def test_gates(self):
        from core.funnel.tools_gate import (
            _FREE_BY_DESIGN, _READ_ONLY_TOOLS)
        self.assertIn("semantic_search_knowledge", _READ_ONLY_TOOLS)
        self.assertIn("knowledge_embedding_status", _READ_ONLY_TOOLS)
        self.assertNotIn("embed_knowledge", _READ_ONLY_TOOLS)
        self.assertEqual(_FREE_BY_DESIGN.get("embed_knowledge"),
                         "knowledge")

    def test_grouped_in_mind(self):
        # The generated snapshot (core/catalogue_snapshot) is the
        # grouped record — gui.skills_catalog_data needs PySide6,
        # which the system interpreter does not have.
        from core.catalogue_snapshot import GROUP_MEMBERS
        mind = GROUP_MEMBERS.get("Mind", ())
        for name in ("semantic_search_knowledge", "embed_knowledge",
                     "knowledge_embedding_status"):
            self.assertIn(name, mind)

    def test_handlers_round_trip(self):
        # Handlers use the default store path — point it at a temp
        # dir so the test never creates the tree's data/ file.
        from core.funnel.tools_registry import tools
        import json as _json
        tmp = tempfile.TemporaryDirectory()
        old = os.environ.get("VYUK_KNOWLEDGE_DIR")
        os.environ["VYUK_KNOWLEDGE_DIR"] = tmp.name
        try:
            # GAPS-23 (F-V7): through the funnel — none of the
            # three is confirm-gated, so no handler is needed.
            status = _json.loads(tools.execute(
                "knowledge_embedding_status", {})["result"])
            self.assertEqual(status["model"], "minilm-l6-v2")
            self.assertIn("embedder", status)
            sem = _json.loads(tools.execute(
                "semantic_search_knowledge",
                {"query": "dog", "limit": 5})["result"])
            self.assertFalse(sem["available"])
            emb = _json.loads(tools.execute(
                "embed_knowledge", {"limit": 5})["result"])
            self.assertFalse(emb["available"])
        finally:
            if old is None:
                os.environ.pop("VYUK_KNOWLEDGE_DIR", None)
            else:
                os.environ["VYUK_KNOWLEDGE_DIR"] = old
            tmp.cleanup()


class _RealOnnxCase(unittest.TestCase):
    """The real MiniLM model through the tree's own producer.

    Runs only on Qt (onnxruntime is Qt-venv-only) with the scratch
    model present; skips everywhere else. A skip is honest — the
    logic is proven by the fake-embedder cases above, and the
    real-model behaviour was measured in the build."""

    def test_real_onnx_minilm(self):
        try:
            import onnxruntime  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("onnxruntime absent on this interpreter")
        scratch = os.environ.get(
            "VYUK_TEST_EMBEDDING_DIR",
            os.path.expanduser(
                "~/workspace/code-lab/fastembed_scratch"))
        model = os.path.join(scratch, "minilm-l6-v2.onnx")
        vocab = os.path.join(scratch, "minilm-vocab.txt")
        if not (os.path.exists(model) and os.path.exists(vocab)):
            self.skipTest("scratch MiniLM model absent")
        from core.funnel.toolbox_embeddings import OnnxEmbedder, _cosine
        from pathlib import Path
        emb = OnnxEmbedder(Path(model), Path(vocab))
        vecs = emb.embed([
            "The dog chased the ball across the garden",
            "A canine ran after the toy in the yard",
            "Quantum entanglement violates local realism",
        ])
        self.assertTrue(all(len(v) == 384 for v in vecs))
        self.assertGreater(_cosine(vecs[0], vecs[1]),
                           _cosine(vecs[0], vecs[2]))
        # And through the store: the real vectors rank the
        # paraphrase above the unrelated doc.
        tmp = tempfile.TemporaryDirectory()
        try:
            store = kn.KnowledgeStore(
                db_path=os.path.join(tmp.name, "k.db"))
            for doc_id, title, passage in DOCS:
                store.add_document(doc_id, title, passage)
            embedded = store.embed_missing(embedder=emb)
            self.assertEqual(embedded["embedded"], 4)
            result = store.semantic_search(
                "a pet dog playing outside", embedder=emb)
            self.assertTrue(result["available"])
            names = [r["doc_id"] for r in result["results"]]
            # Both dog docs on top (their internal order is the
            # model's own — the canine paraphrase scores first
            # here); the quantum and bread docs are excluded.
            self.assertEqual(set(names[:2]), {"doc-dog", "doc-canine"})
            self.assertNotIn("doc-quantum", names)
            self.assertNotIn("doc-bread", names)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
