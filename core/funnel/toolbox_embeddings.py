"""Split out of core/funnel/toolbox.py (B37 part 2 of 2).

Embeddings family: tiny ONNX model, cosine ranking, ranking blend.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

# B37 INTENTIONAL: embeddings blends the keyword ranking, so it imports
# keyword_scores + tool_search_text from the tokens module (same objects).
from .toolbox_tokens import keyword_scores, tool_search_text


# ---------------------------------------------------------------------------
# B6(b) (v5.45): tiny embedding model — cosine tool ranking.
#
# Model: all-MiniLM-L6-v2 ONNX (quantisation-free export, ~90 MB)
# + its vocab.txt, provisioned EXACTLY like core/voice/local_assets
# provisions its assets: a manifest with a size floor (and, since
# v5.47 programme item 17, a sha256 pin per file), and the
# resumable download_file (`.part` + Range resume) borrowed from
# local_assets at call time. Files live under data/embeddings/;
# tool embeddings are computed once and cached there as JSON
# (keyed by a hash of every tool's search text, so a registry or
# synonym change invalidates the cache).
#
# GATE: this is an enhancement, never a dependency. No model on
# disk, no onnxruntime, a failed download, a corrupt cache —
# every failure path returns None and rank_tools silently uses
# the (a) keyword ranking. Nothing here raises into a caller.
# ---------------------------------------------------------------------------

import hashlib
import math
from pathlib import Path

EMBEDDING_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "embeddings"  # Phase 3 F2a: file now in core/funnel/ — one more level up to the package root
# v5.47 (programme item 17 / C6b): each entry now also carries a
# "sha256" pin, computed with sha256sum from the REAL file as
# downloaded from its URL on 2026-10-23 (measure-the-genuine-bytes
# provenance, the local_assets DOWNLOAD_SHA256 discipline).
# Readiness is size floor AND hash: a same-size corrupted or
# substituted model must NOT count as ready — it is re-fetched by
# ensure_embedding_model() or rejected, in this module's
# never-raise failure style.
EMBEDDING_MANIFEST = {
    "model": {
        "file": "minilm-l6-v2.onnx",
        "url": ("https://huggingface.co/sentence-transformers/"
                "all-MiniLM-L6-v2/resolve/main/onnx/model.onnx"),
        "min_bytes": 50_000_000,
        "sha256": "6fd5d72fe4589f189f8ebc006442dbb529bb7ce38f8082112682524616046452",
    },
    "vocab": {
        "file": "minilm-vocab.txt",
        "url": ("https://huggingface.co/sentence-transformers/"
                "all-MiniLM-L6-v2/resolve/main/vocab.txt"),
        "min_bytes": 100_000,
        "sha256": "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3",
    },
}
_EMBEDDING_CACHE_FILE = "tool_embeddings.json"
_embedder_singleton = None
_embedder_failed = False


def embedding_paths() -> dict[str, Path]:
    return {key: EMBEDDING_DIR / spec["file"]
            for key, spec in EMBEDDING_MANIFEST.items()}


def _embedding_file_sha256(path: Path) -> str | None:
    """Streaming SHA-256 hex digest of one embedding asset, or
    None when it cannot be read (v5.47, programme item 17)."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _embedding_file_ready(key: str, path: Path) -> bool:
    """One embedding asset's readiness: over its size floor AND
    matching its manifest sha256 pin when one is recorded
    (v5.47, programme item 17). Never raises."""
    spec = EMBEDDING_MANIFEST[key]
    try:
        if path.stat().st_size <= spec["min_bytes"]:
            return False
    except OSError:
        return False
    pin = spec.get("sha256")
    if pin:
        return _embedding_file_sha256(path) == pin
    return True


def embedding_model_ready() -> bool:
    """Size-floor AND hash-pin readiness, local_assets style
    (hash pins added v5.47, programme item 17: a mismatched
    on-disk file does not count as ready)."""
    for key, path in embedding_paths().items():
        if not _embedding_file_ready(key, path):
            return False
    return True


def ensure_embedding_model(progress_cb=None) -> bool:
    """Downloads the embedding assets when missing, through
    local_assets.download_file (resumable). Returns readiness.
    Never raises."""
    if embedding_model_ready():
        return True
    try:
        from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/; Phase 3 F2a: toolbox now in core/funnel/

        EMBEDDING_DIR.mkdir(parents=True, exist_ok=True)
        for key, spec in EMBEDDING_MANIFEST.items():
            dest = EMBEDDING_DIR / spec["file"]
            # v5.47 (programme item 17): the skip test is the full
            # readiness test (size floor AND hash pin), so a
            # corrupted on-disk file is RE-FETCHED here instead of
            # being skipped on size alone and failing readiness
            # forever; download_file replaces it atomically.
            if _embedding_file_ready(key, dest):
                continue
            err = local_assets.download_file(
                spec["url"], dest,
                progress_cb or (lambda _line: None),
                label=f"tool embedding {key}")
            if err is not None:
                print(f"[Toolbox] Embedding {key} download failed: {err}")
                return False
        return embedding_model_ready()
    except Exception as e:  # noqa: BLE001 — degrade to keyword ranking
        print(f"[Toolbox] Embedding provisioning unavailable: {e}")
        return False


class _WordPiece:
    """Minimal BERT WordPiece tokenizer over vocab.txt — enough
    for MiniLM inference (lowercase, punctuation split, greedy
    longest-match subwords, [UNK] fallback)."""

    def __init__(self, vocab_path: Path):
        self.vocab: dict[str, int] = {}
        with open(vocab_path, encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                self.vocab[line.rstrip("\n")] = idx
        self.unk = self.vocab.get("[UNK]", 100)

    def encode(self, text: str, max_len: int = 128) -> list[int]:
        import unicodedata

        lowered = unicodedata.normalize("NFD", (text or "").lower())
        basic: list[str] = []
        for ch in lowered:
            if unicodedata.category(ch) == "Mn":
                continue
            if ch.isalnum() or ch == "_":
                basic.append(ch)
            else:
                basic.append(" ")
                basic.append(ch)
                basic.append(" ")
        ids = [self.vocab.get("[CLS]", 101)]
        for word in "".join(basic).split():
            ids.extend(self._wordpiece(word))
        ids.append(self.vocab.get("[SEP]", 102))
        return ids[:max_len]

    def _wordpiece(self, word: str) -> list[int]:
        if len(word) > 40:
            return [self.unk]
        out: list[int] = []
        start = 0
        while start < len(word):
            end = len(word)
            found = None
            while end > start:
                piece = word[start:end]
                if start > 0:
                    piece = "##" + piece
                if piece in self.vocab:
                    found = self.vocab[piece]
                    break
                end -= 1
            if found is None:
                return [self.unk]
            out.append(found)
            start = end
        return out


class OnnxEmbedder:
    """all-MiniLM-L6-v2 through onnxruntime: mean-pooled,
    L2-normalised sentence embeddings. Construction raises on
    any problem — get_embedder() converts that to None."""

    def __init__(self, model_path: Path, vocab_path: Path):
        import numpy as np
        import onnxruntime as ort

        self._np = np
        self._tokenizer = _WordPiece(vocab_path)
        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"])
        self._input_names = {i.name for i in self._session.get_inputs()}

    def embed(self, texts: list[str]) -> list[list[float]]:
        np = self._np
        results: list[list[float]] = []
        for text in texts:
            ids = self._tokenizer.encode(text)
            feed = {}
            if "input_ids" in self._input_names:
                feed["input_ids"] = np.array([ids], dtype=np.int64)
            if "attention_mask" in self._input_names:
                feed["attention_mask"] = np.ones(
                    (1, len(ids)), dtype=np.int64)
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros(
                    (1, len(ids)), dtype=np.int64)
            hidden = self._session.run(None, feed)[0][0]
            mask = np.ones(len(ids), dtype=np.float32)
            pooled = (hidden * mask[:, None]).sum(axis=0) / max(mask.sum(), 1.0)
            norm = float(np.linalg.norm(pooled)) or 1.0
            results.append([float(x) for x in (pooled / norm)])
        return results


def get_embedder():
    """The process-wide embedder, or None when unavailable
    (the gate: any failure degrades silently, once)."""
    global _embedder_singleton, _embedder_failed
    if _embedder_singleton is not None:
        return _embedder_singleton
    if _embedder_failed:
        return None
    try:
        if not embedding_model_ready():
            _embedder_failed = True
            return None
        paths = embedding_paths()
        _embedder_singleton = OnnxEmbedder(paths["model"], paths["vocab"])
        return _embedder_singleton
    except Exception as e:  # noqa: BLE001
        print(f"[Toolbox] Embedding model unavailable ({e}); "
              "keyword ranking only.")
        _embedder_failed = True
        return None


def set_embedder(embedder) -> None:
    """Injection seam for tests/evals: install a fake (or real)
    embedder; pass None to clear it and restore the on-disk
    behaviour (get_embedder re-probes the model files)."""
    global _embedder_singleton, _embedder_failed
    _embedder_singleton = embedder
    _embedder_failed = False


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _tool_embedding_vectors(registry: Any, embedder) -> dict[str, list[float]]:
    """Tool name -> embedding, cached under data/embeddings/
    keyed by a hash of all search texts. A bad cache is ignored
    (recomputed), never fatal."""
    decls = [d for d in registry.get_declarations()
             if d["name"] != "use_toolbox"]
    fingerprint_src = "minilm-v2-128tok\n" + "\n".join(
        f"{d['name']}|{tool_search_text(d)}" for d in decls)
    fingerprint = hashlib.sha256(fingerprint_src.encode()).hexdigest()
    cache_path = EMBEDDING_DIR / _EMBEDDING_CACHE_FILE
    # Only the real ONNX embedder may use the on-disk cache: an
    # injected fake's vectors must never be persisted as if the
    # model had produced them.
    use_cache = isinstance(embedder, OnnxEmbedder)
    if not use_cache:
        texts = [tool_search_text(d) for d in decls]
        vectors = embedder.embed(texts)
        return {d["name"]: v for d, v in zip(decls, vectors)}
    try:
        import json as _json

        cached = _json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("fingerprint") == fingerprint:
            return {k: [float(x) for x in v]
                    for k, v in cached.get("vectors", {}).items()}
    except Exception:  # noqa: BLE001 — cache is a nicety
        pass
    texts = [tool_search_text(d) for d in decls]
    vectors = embedder.embed(texts)
    out = {d["name"]: v for d, v in zip(decls, vectors)}
    try:
        import json as _json

        EMBEDDING_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(_json.dumps(
            {"fingerprint": fingerprint, "vectors": out}),
            encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return out


def semantic_scores(registry: Any, query: str, embedder=None) -> dict[str, float]:
    """Cosine similarity per tool, or {} when no embedder is
    available (the degrade path — callers must treat {} as
    'semantic ranking unavailable', never as an error)."""
    embedder = embedder if embedder is not None else get_embedder()
    if embedder is None or not (query or "").strip():
        return {}
    try:
        vectors = _tool_embedding_vectors(registry, embedder)
        query_vec = embedder.embed([query])[0]
    except Exception as e:  # noqa: BLE001
        print(f"[Toolbox] Semantic ranking failed ({e}); keyword only.")
        return {}
    return {name: _cosine(query_vec, vec) for name, vec in vectors.items()}


def rank_tools_semantic(registry: Any, query: str, limit: int = 8,
                        embedder=None) -> list[str]:
    """Pure B6(b) ranking: cosine order, keyword score as the
    tiebreak, registry order last. [] when semantics are
    unavailable."""
    cos = semantic_scores(registry, query, embedder=embedder)
    if not cos:
        return []
    kw = keyword_scores(registry, query)
    order = {d["name"]: i for i, d in enumerate(registry.get_declarations())}
    ranked = sorted(cos, key=lambda n: (-cos[n], -kw.get(n, 0), order.get(n, 0)))
    return ranked[:limit]


def rank_tools(registry: Any, query: str, limit: int = 8) -> list[str]:
    """Ranks the registry against a few words of intent.

    B6 (v5.45): keyword score (stemmed, synonym-expanded — see
    keyword_scores) blended with the embedding cosine when the
    tiny model is available: combined = keyword + 100 * max(0,
    cosine). The weight was picked on the fixed eval set (see
    test_v545_b6): pure cosine ranking measured WORSE than
    keyword alone on this registry (long tool descriptions
    dilute the embedding), while the blend beat keyword-only
    top-1 at every weight tried (20/40/60/100), best at 100.
    Any embedding failure degrades to the keyword ranking
    alone, silently. Ties keep registry order, so the ranking
    stays stable and testable. use_toolbox never ranks."""
    combined = ranking_scores(registry, query)
    if not combined:
        return []
    order = {d["name"]: i for i, d in enumerate(registry.get_declarations())}
    ranked = sorted(combined, key=lambda n: (-combined[n], order.get(n, 0)))
    return ranked[:limit]


def ranking_scores(registry: Any, query: str) -> dict[str, float]:
    """The blended score per tool that rank_tools() sorts by:
    keyword score + 100 * max(0, cosine) when the embedding
    model is available, the keyword score alone when it is not
    (see rank_tools for how the blend weight was picked).

    v5.50 (programme item 45): factored out of rank_tools so the
    find confidence rule (find_is_confident, below) judges the
    EXACT numbers the ranking itself used — one computation, two
    readers, no drift between them. {} when nothing scored."""
    kw = keyword_scores(registry, query)
    cos = semantic_scores(registry, query)
    if not kw and not cos:
        return {}
    names = set(kw) | {n for n, c in cos.items() if c > 0.05}
    return {
        n: kw.get(n, 0) + 100.0 * max(0.0, cos.get(n, 0.0))
        for n in names
    }
