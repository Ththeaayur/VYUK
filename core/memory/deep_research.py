"""Deep-research tool implementation (research wiring batch).

Wires ``research_loop`` (core/memory/research_edition_deltas.py, A6-pattern kernel)
to a real production caller: the ``deep_research`` tool, reached
through the normal funnel (validation, confirm posture for reads,
budget, gate stack). The retriever is the LOCAL KnowledgeStore
only (FTS5/LIKE search over VYUK's own stored documents) — no
network, no fetcher, no model generation, no executor access.
The summary is extractive (Wave 6 ``summarise_without_executor``)
and citations come from the retrieval log alone.

Design notes, all deliberate:
- The retriever is stateful per call (a seen-set closure): the
  kernel calls retrieve with the SAME question every round, so
  without dedup rounds 2-3 would triple-count the same docs.
  With dedup, round 2 returns [] and the kernel breaks early —
  each stored doc appears in the log exactly once.
- Empty store, honestly labelled: when the loop's log is empty
  the result says so plainly (``matched: 0`` + a no-findings
  note) instead of presenting an empty summary as findings.
- Store failure, honestly unavailable: a KnowledgeStore that
  cannot open or search raises out of the loop (the kernel has
  no try around retrieve by design); this module catches it and
  returns ``available: False`` with the reason — never a fake
  result, never a silent empty result.
- ``research_loop`` is referenced via module attribute
  (``research_edition_deltas.research_loop``) so tests can observe the
  call; there is exactly ONE call site in this module (the
  capability suite pins that).
"""

from . import knowledge
from . import research_edition_deltas

__all__ = ["make_local_retriever", "run_deep_research"]


def make_local_retriever(store=None):
    """Build the local-only retriever for one loop run.

    ``store`` defaults to a fresh KnowledgeStore (DATA_DIR). The
    returned callable takes a query string and returns doc dicts
    in the kernel's shape (doc_id/title/passage/source/licence),
    each doc at most once per run (seen-set dedup across rounds).
    """
    store = store if store is not None else knowledge.KnowledgeStore()
    seen = set()

    def retrieve(query):
        result = store.search(str(query or ""), limit=10)
        docs = []
        for row in (result or {}).get("results", []):
            doc_id = str(row.get("doc_id", ""))
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            docs.append({
                "doc_id": doc_id,
                "title": row.get("title", ""),
                "passage": row.get("passage", ""),
                "source": row.get("source", ""),
                "licence": row.get("licence", ""),
            })
        return docs

    return retrieve


def run_deep_research(question, max_rounds=3, store=None, retrieve=None):
    """Run the A6 deep-research loop over local stored docs.

    ``question`` is the research question. ``retrieve``, when
    given, replaces the local retriever (tests inject a stub);
    otherwise the local KnowledgeStore retriever is built.
    ``store`` is passed to the retriever builder (tests pass a
    temp store; production passes none). Returns the kernel
    result plus ``matched`` and, when nothing matched, an
    explicit no-findings note. Store failure -> honest
    unavailable record, never an exception to the caller.
    """
    question = str(question or "")
    if not question.strip():
        return {"available": False,
                "reason": "a research question is required"}
    if retrieve is None:
        try:
            retrieve = make_local_retriever(store=store)
        except Exception as exc:  # store cannot even be built
            return {"available": False,
                    "reason": f"knowledge store unavailable: {exc}"}
    try:
        result = research_edition_deltas.research_loop(
            question, retrieve, max_rounds=max_rounds)
    except Exception as exc:  # store failed mid-loop
        return {"available": False,
                "reason": f"knowledge store unavailable: {exc}"}
    if not isinstance(result, dict) or not result.get("retrieval_log"):
        # Loop unavailable (retrieve None can never happen here —
        # retrieve is always built or injected — but stay honest
        # if the kernel ever reports it) or nothing matched.
        if isinstance(result, dict) and result.get("available") is False:
            return result
        return {
            "available": True,
            "question": question,
            "kernel": "A6-pattern (local-only)",
            "rounds_run": result.get("rounds_run", 0)
            if isinstance(result, dict) else 0,
            "matched": 0,
            "retrieval_log": [],
            "summary": "",
            "citations_from_log": [],
            "executor_access": False,
            "note": "no stored documents matched the question — "
                    "this is a statement about VYUK's local store "
                    "only, never a claim about the wider literature",
        }
    result = dict(result)
    result["matched"] = len(result["retrieval_log"])
    return result
