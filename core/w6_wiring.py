"""Conversion pass — Wave 6 (v5.61, "Memory & Knowledge") wiring coordinator.

Wires the staged Wave 6 modules into live paths WITHOUT new tool
declarations, WITHOUT a VERSION bump, WITHOUT a requirements.txt
change, WITHOUT touching any locked file, and WITHOUT touching
core/w7_wiring.py or core/w8_wiring.py. Every seam here keeps the
staged module's own safety shape, and every gate below fails closed.

Per-item truth (Phase 1 audit, NOT the wave's claims):

* Item 1, file-backed memory store — ALREADY LIVE. The dual-read
  in core/memory/memory.py (_dual_read_facts, called from
  get_memory_context_prompt, itself called by live_client,
  local_client and cloud_text_client) is untouched by this
  module. Nothing here re-wires it; the suite only re-verifies
  the call chain is still present.
* Item 2, KN-4 retrieval — WIRED AS A REGISTRATION-READY PATH.
  knowledge_search() / knowledge_prompt_lines() call
  KnowledgeStore.search directly (FTS5, LIKE fallback when FTS5
  is absent, vector lane only when sqlite-vec actually loads).
  Embedder tiers stay facade-only: embedder_status() reports
  every seat as absent when its model is absent, and NO model
  is presented as live. The tool-shaped wrappers in
  W6_TOOL_HANDLERS are complete and tested, but their tool
  declarations + dispatch in core/funnel/tools_handlers.py are
  REGISTRATION-READY, pending the parallel Stage 2 leaf landing
  (tools_handlers.py is FILE-LOCKED for this leaf) — see
  REGISTRATION_OWED. No declaration is added here, so the
  fitted payload is unchanged.
* Item 3, generative extraction / GLiNER bakeoff — PLUMBING-ONLY.
  The bakeoff harness (run_gliner_bakeoff) and the confidence
  gate (ingest_triples -> gate_triples) are wired as plumbing,
  but the bakeoff verdict offscreen is NO-DATA (winner None:
  the GLiNER leg is unmeasured, weights absent), so
  ingest_via_generative_extractor stays REFUSED exactly as the
  staged module refuses it — generative_ingest_refusal()
  returns that refusal verbatim, never a fabricated ingest.
  Winner decision: OWED (see EXTRACTION_WINNER_OWED).
* Item 4, retention scheduler — WIRED (live tick). The staged
  module contains due-date arithmetic only, NO scheduler loop,
  so this module builds the minimal tick-based scheduler:
  retention_tick() calls due_for_reverification() over the
  live file store and surfaces stale flags (stale_flag()), and
  w6_tick() runs it on the live RoutineScheduler tick via the
  hook in core/brain/routine_scheduler.py, alongside (never
  disturbing) the Wave 7 daily_tick hook. Stale facts still
  surface in recall — the tick flags them, it never deletes
  or auto-confirms anything.
* Item 5, research clients — WIRED AS A REGISTRATION-READY
  PATH. research_entry() puts Crossref / OpenAlex / arXiv
  behind one entry point with a caller-supplied fetcher (no
  fetcher = honest unavailable, never a sent request), a
  per-article licence gate (article_licence_gate: metadata
  always allowed, full text only when the article's own
  licence permits, PDF serve ALWAYS refused), retrieved-data
  tagging (tag_retrieved: data, never instructions), and
  answer_from_docs extractive answers with citations from
  the retrieval log only. Peer-review refusal
  (refuse_peer_review) stays in force, disclosure flags stay
  surfaced, never auto-filled. Tool-shaped wrapper: see
  REGISTRATION_OWED.
* Item 6, people / promises / opinions — WIRED (tick surfacing)
  + REGISTRATION-READY (writes). remember_person() /
  remember_promise() write through the dual-read file store
  (opinions ONLY through write_opinion, never promoted to
  facts — the prompt never renders opinions), and
  overdue_promises surface on the live routine tick via
  w6_tick(). The write wrappers' tool registrations are
  REGISTRATION-READY (same locked-file reason as item 2).
  Pressure-valve behaviour: DECISION-OWED — this module
  reports the store size vs PRESSURE_VALVE_MAX and NEVER
  runs pressure_valve() on its own (see
  PRESSURE_VALVE_DECISION_OWED).
* Item 7, PF-3 API surface — HANDLER-ONLY, VERIFIED INTACT.
  list_stored_facts / delete_one / wipe_all /
  export_milestones in core/privacy.py are intact and
  their signatures are asserted by the suite, but they gain
  NO new caller here: their closing step is the Wave 9 PF
  Privacy Dashboard widget, which belongs to the Wave 9
  leaf, NOT to this module. PF-3 criterion gap CARRIED
  explicitly: delete-everywhere is NOT met (file records
  only; DB rows are rollback material) — decision owed +
  per-row expiry dates owed at wiring time (see
  PF3_GAP_OWED). Nothing here invents an everywhere-delete.

GATED / NOT-WIRED items are named, never silent:
EXTRACTION_WINNER_OWED (GLiNER bakeoff winner), 
PRESSURE_VALVE_DECISION_OWED, PF3_GAP_OWED,
REGISTRATION_OWED (tool declarations + dispatch, locked file),
and EMBEDDER_ADOPTION_OWED (no embedder adopted).

This module imports staged Wave 6 modules + stdlib only at
module level (no brain, no tool registry, no GUI), so
importing it can never create a cycle or a live side effect.
It declares no tool and adds zero fitted bytes.
"""

from __future__ import annotations

import datetime
import inspect
from pathlib import Path
from typing import Any, Callable, Optional

from . import (
    extraction,
)
from .memory import (  # Phase 3 M1b: moved to core/memory/
    people,
    privacy,
    retention,
)
# Phase 3 M1a: knowledge + research_stack moved to core/memory/
from .memory import knowledge, research_stack
from .memory.memory_files import list_facts as _list_facts

WIRING_VERSION = "conversion-w6"

# --- Owed decisions / gates, stated in code so they cannot drift --

EXTRACTION_WINNER_OWED = (
    "GLiNER bakeoff winner undecided (NO-DATA offscreen: GLiNER "
    "weights absent, GLiNER leg unmeasured). Generative-extractor "
    "ingestion stays REFUSED until a winner is measured on "
    "conversational EN/HI in a live Windows trial; the gate "
    "(gate_triples) is wired as plumbing only, never as an "
    "adopted extractor."
)
PRESSURE_VALVE_DECISION_OWED = (
    "Pressure-valve behaviour needs an owner decision: when the "
    "file store exceeds PRESSURE_VALVE_MAX records, which "
    "oldest non-pinned, non-promise records archive (and with "
    "what user notice) is not decided here, so this module "
    "reports the size vs the cap and never runs "
    "people.pressure_valve() on its own."
)
PF3_GAP_OWED = (
    "PF-3 closes at Wave 9 wiring (PF Privacy Dashboard "
    "widget). Criterion gap carried: delete-everywhere is NOT "
    "met — the Wave 6 API deletes file records only; DB rows "
    "are rollback material. Decision owed on everywhere-delete "
    "semantics + per-row expiry dates owed at wiring time."
)
REGISTRATION_OWED = (
    "Tool declarations + dispatch for knowledge_search, "
    "research, remember_person and remember_promise are "
    "REGISTRATION-READY, pending the parallel Stage 2 leaf "
    "landing: the complete handler functions live in "
    "W6_TOOL_HANDLERS in this module and are proven by "
    "test_conv_w6.py, but core/funnel/tools_handlers.py is "
    "FILE-LOCKED for this leaf, so no declaration or dispatch "
    "is added here. At registration time the coordinator must "
    "measure each new declaration's fitted bytes FIRST "
    "(fitted budget INV-1: 229,785 / 240,000 B at this leaf's "
    "baseline) and only register what fits — anything that "
    "does not fit goes to overflow_queue.md, never busting "
    "the budget."
)
EMBEDDER_ADOPTION_OWED = (
    "MiniLM producer adopted for the KnowledgeStore (R8-1 "
    "fastembed PORT, not adopt): the in-tree ONNX embedder "
    "(toolbox_embeddings, all-MiniLM-L6-v2, operator-supplied, "
    "pinned sha256) fills the embeddings table and semantic "
    "search ranks by cosine over it. The E5-small, Model2Vec, "
    "Qwen3-Embedding and BGE-M3 head-to-head seats still report "
    "available=False (weights absent) — they are a bake-off, not "
    "the producer. The MiniLM model licence is PARENT-RULED "
    "Apache-2.0 (R8-1, 2026-11-10); the model stays "
    "operator-supplied and is never bundled (bundling is a "
    "future model-distribution-gate decision)."
)
MEMORY_DUAL_READ_EVIDENCE = (
    "core/memory/memory.py:288 imports dual_read/file_store_has_records/"
    "migrate_if_needed; :274 _dual_read_facts calls them; "
    ":318 inside get_memory_context_prompt calls _dual_read_facts; "
    "called by live_client.py:923, local_client.py:669,671, "
    "cloud_text_client.py:723 (verified by grep in the suite)."
)

# ---------------------------------------------------------------------------
# Configurable bases (tests redirect; production uses module defaults)
# ---------------------------------------------------------------------------

_W6_BASE: Optional[str] = None
_LAST_W6_TICK: dict = {
    "retention": {"due": [], "stale_count": 0, "lines": []},
    "overdue_promises": [],
    "lines": [],
}


def configure_w6_base(base) -> None:
    """Redirect the file-store base for tick reads/writes (tests)."""
    global _W6_BASE
    _W6_BASE = None if base is None else str(base)


def reset_w6_state() -> None:
    """Reset tick reports + base override (tests)."""
    global _LAST_W6_TICK
    configure_w6_base(None)
    _LAST_W6_TICK = {
        "retention": {"due": [], "stale_count": 0, "lines": []},
        "overdue_promises": [],
        "lines": [],
    }


def last_w6_tick_report() -> dict:
    return _LAST_W6_TICK


# ---------------------------------------------------------------------------
# Item 2 — KN-4 retrieval substrate
# ---------------------------------------------------------------------------

def knowledge_store(db_path=None) -> knowledge.KnowledgeStore:
    return knowledge.KnowledgeStore(db_path=db_path)


def embedder_status() -> dict:
    """Honest facade report: every seat absent while weights are absent."""
    seats = {}
    for seat in (
        knowledge.E5_SMALL,
        knowledge.MODEL2VEC,
        knowledge.QWEN3_EMBEDDING,
        knowledge.BGE_M3,
    ):
        result = seat.embed(["probe"])
        seats[seat.name] = {
            "available": bool(result["available"]),
            "dims": seat.dims,
            "reason": result["reason"],
        }
    return {
        "seats": seats,
        "any_available": any(s["available"] for s in seats.values()),
        "vector_available": knowledge.sqlite_vec_available(),
        "fts5_available": knowledge.fts5_available(),
        "adoption_owed": EMBEDDER_ADOPTION_OWED,
    }


def knowledge_search(query: str, limit: int = 10, db_path=None) -> dict:
    """Live retrieval path: KnowledgeStore.search, lanes disclosed."""
    store = knowledge_store(db_path=db_path)
    result = store.search(query, limit=limit)
    result["embedder"] = embedder_status()
    return result


def knowledge_semantic_search(query: str, limit: int = 10,
                              db_path=None) -> dict:
    """R8-1 fastembed port: pure semantic search over the
    KnowledgeStore (cosine over the embeddings table). Honest
    when the model or the vectors are absent — the record says
    so, never a fabricated ranking."""
    store = knowledge_store(db_path=db_path)
    result = store.semantic_search(query, limit=limit)
    result["embedder"] = embedder_status()
    return result


def knowledge_embed_missing(limit: int = 50, db_path=None) -> dict:
    """R8-1 fastembed port: embed every document with no vector
    for the current model. Bounded work per call (default 50)."""
    store = knowledge_store(db_path=db_path)
    return store.embed_missing(limit=limit)


def knowledge_embedding_status(db_path=None) -> dict:
    """R8-1 fastembed port: how full the vector index is, and
    whether semantics can run — plus the honest seat report."""
    store = knowledge_store(db_path=db_path)
    result = store.vector_index_status()
    result["embedder"] = embedder_status()
    return result


def knowledge_add_document(doc_id, title, passage, source="",
                           licence="", db_path=None) -> dict:
    store = knowledge_store(db_path=db_path)
    rowid = store.add_document(doc_id, title, passage,
                               source=source, licence=licence)
    return {"rowid": rowid, "doc_id": doc_id, "licence": licence}


def search_documents(query: str, limit: int = 10, db_path=None,
                     embedder=None) -> dict:
    """Darwin D1: semantic-first search over the user's documents
    only (the disk-index bridge). Lanes disclosed; with no model
    the FTS lane runs and the record says semantics was
    unavailable — never a fabricated ranking."""
    from . import document_memory

    return document_memory.search_documents(
        query, limit=limit, db_path=db_path, embedder=embedder)


def list_documents(limit: int = 10, offset: int = 0,
                   db_path=None) -> dict:
    """Darwin D1: the indexed-document list for the Memory page
    (one row per file) plus the embedding status, so the page can
    say honestly whether semantic search is available."""
    from . import document_memory

    return document_memory.list_documents(
        limit=limit, offset=offset, db_path=db_path)


def forget_documents(source: str, db_path=None) -> dict:
    """Darwin D1: forget one document, or a whole folder (which
    also drops it from the remembered index roots, so a later
    refresh cannot re-index it)."""
    from . import document_memory

    return document_memory.forget_documents(source, db_path=db_path)


def knowledge_rate_document(doc_id, rating, note="", db_path=None) -> dict:
    store = knowledge_store(db_path=db_path)
    return store.rate_document(doc_id, rating, note=note)


def knowledge_prompt_lines(query: str, limit: int = 5,
                           db_path=None) -> list[str]:
    """Prompt-assembly slot: citation lines from a live search.

    This is the retrieval path's prompt slot — a caller folds
    these lines into brain context (like the memory block).
    Lines carry doc_id + source + licence so a citation is
    never separated from its provenance.
    """
    result = knowledge_search(query, limit=limit, db_path=db_path)
    lines = []
    for item in result.get("results", []):
        lines.append(
            f"[{item.get('doc_id', '')}] {item.get('title', '')}: "
            f"{item.get('passage', '')} "
            f"(source: {item.get('source', '')}; "
            f"licence: {item.get('licence', '')})"
        )
    return lines


# ---------------------------------------------------------------------------
# Item 3 — Generative extraction / GLiNER bakeoff (plumbing only)
# ---------------------------------------------------------------------------

def run_gliner_bakeoff(gold, incumbent_predicted) -> dict:
    """Bakeoff harness, wired as plumbing. Winner stays None offscreen."""
    verdict = extraction.gliner_bakeoff(gold, incumbent_predicted)
    verdict["winner_decision_owed"] = EXTRACTION_WINNER_OWED
    return verdict


def ingest_triples(triples) -> dict:
    """Confidence-gated triple path (the writable path, NOT generative).

    Valid triples at/above threshold write; valid triples below
    threshold are HELD for confirmation; invalid triples are
    dropped outright. Returns the gate split, never a claim
    that triples were written to a store — writing is the
    caller's step, through the file store.
    """
    gated = extraction.gate_triples(triples)
    gated["generative_ingestion"] = "refused until bakeoff winner"
    return gated


def generative_ingest_refusal(text: str) -> dict:
    """The staged refusal, returned verbatim — the gate, not a feature."""
    return extraction.ingest_via_generative_extractor(text)


def extraction_status() -> dict:
    return {
        "winner": None,
        "generative_ingestion_available": False,
        "gate_wired": True,
        "winner_decision_owed": EXTRACTION_WINNER_OWED,
    }


# ---------------------------------------------------------------------------
# Item 4 — Retention scheduler (the loop the staged module lacks)
# ---------------------------------------------------------------------------

def retention_tick(base=None, today=None) -> dict:
    """One retention pass: due facts + stale flags, never a delete."""
    today = today or datetime.date.today().isoformat()
    records, _skipped = _list_facts(base=base if base is not None else _W6_BASE)
    due = retention.due_for_reverification(records, today=today)
    flagged = []
    for record in due:
        flagged.append({
            "category": record.get("category", ""),
            "key": record.get("key", ""),
            "due_date": record.get("due_date", ""),
            "flag": retention.stale_flag(record, today=today),
            "value": record.get("value", ""),
        })
    lines = [
        f"Re-check overdue: {item['category']}/{item['key']} "
        f"(due {item['due_date']}){item['flag']}"
        for item in flagged
    ]
    return {"due": flagged, "stale_count": len(flagged), "lines": lines,
            "today": today}


def mark_fact_reverified(record, today=None) -> dict:
    """Re-verification arithmetic wrapper (caller writes it back)."""
    return retention.mark_reverified(record, today=today)


# ---------------------------------------------------------------------------
# Item 6 — People / promises / opinions
# ---------------------------------------------------------------------------

def remember_person(name, relationship="", note="", base=None) -> dict:
    path = people.write_person(
        name, relationship=relationship, note=note,
        base=base if base is not None else _W6_BASE)
    return {"path": str(path), "name": name}


def remember_promise(person, promise_text, due_date="", base=None) -> dict:
    path = people.write_promise(
        person, promise_text, due_date=due_date,
        base=base if base is not None else _W6_BASE)
    return {"path": str(path), "person": person, "due_date": due_date}


def remember_opinion(topic, opinion_text, base=None) -> dict:
    """Opinions ONLY through this path; never promoted to facts."""
    path = people.write_opinion(
        topic, opinion_text,
        base=base if base is not None else _W6_BASE)
    return {"path": str(path), "topic": topic, "kind": "opinion"}


def list_people_w6(base=None) -> list:
    return people.list_people(
        base=base if base is not None else _W6_BASE)


def overdue_promises_w6(today=None, base=None) -> list:
    today = today or datetime.date.today().isoformat()
    return people.overdue_promises(
        today, base=base if base is not None else _W6_BASE)


def pressure_valve_status(base=None) -> dict:
    """Read-only size report. NEVER runs pressure_valve() (decision owed)."""
    records, _skipped = _list_facts(base=base if base is not None else _W6_BASE)
    return {
        "records": len(records),
        "max": people.PRESSURE_VALVE_MAX,
        "over_cap": len(records) > people.PRESSURE_VALVE_MAX,
        "action_taken": None,
        "decision_owed": PRESSURE_VALVE_DECISION_OWED,
    }


# ---------------------------------------------------------------------------
# Live routine tick: retention + overdue promises (alongside Wave 7)
# ---------------------------------------------------------------------------

def w6_tick(now=None, base=None, today=None) -> dict:
    """One Wave 6 tick, called by the live RoutineScheduler tick.

    Cheap when the store is absent (list_facts returns empty on
    a missing dir, no file is created). Failures here must
    never break routine scheduling — the caller wraps this in
    try/except, and this function itself never raises for a
    missing/empty store.
    """
    global _LAST_W6_TICK
    if today is None:
        if isinstance(now, datetime.datetime):
            today = now.date().isoformat()
        elif isinstance(now, datetime.date):
            today = now.isoformat()
        else:
            today = datetime.date.today().isoformat()
    effective_base = base if base is not None else _W6_BASE
    retention = retention_tick(base=effective_base, today=today)
    overdue = overdue_promises_w6(today=today, base=effective_base)
    lines = list(retention["lines"])
    for promise in overdue:
        lines.append(
            f"Overdue promise to {promise.get('key', '')}: "
            f"{promise.get('value', '')} (due {promise.get('due_date', '')})"
        )
    _LAST_W6_TICK = {
        "retention": retention,
        "overdue_promises": overdue,
        "lines": lines,
    }
    return _LAST_W6_TICK


# ---------------------------------------------------------------------------
# Item 5 — Research clients (licence-gated entry point)
# ---------------------------------------------------------------------------

def _gate_articles(articles) -> list:
    gated = []
    for article in articles or []:
        if not isinstance(article, dict):
            continue
        gate = research_stack.article_licence_gate(article)
        gated.append({**article, "licence_gate": gate})
    return gated


def research_entry(question: str, source: str = "arxiv", *,
                   fetcher=None, doi: str = "", daily_cap=None,
                   max_results: int = 10, db_path=None) -> dict:
    """Live research entry point. No fetcher = honest unavailable.

    Sources: 'arxiv' (metadata/digest only, PDF serve refused),
    'crossref' (needs a DOI; metadata CC0-class), 'openalex'
    (needs key + caller-set daily cap, both enforced by the
    client). Every returned article carries its licence gate;
    retrieved content is tagged DATA-only, never executable.
    """
    source = str(source or "arxiv").lower()
    if source == "arxiv":
        client = research_stack.ArxivClient(fetcher=fetcher)
        raw = client.search(question, max_results=max_results)
        if isinstance(raw, str):
            articles = research_stack.ArxivClient.parse_atom(raw)
        elif isinstance(raw, dict) and "results" in raw:
            articles = raw["results"]
        else:
            articles = raw if isinstance(raw, list) else []
            if isinstance(raw, dict) and raw.get("available") is False:
                return {**raw, "source": "arxiv",
                        "licence_gated_articles": [],
                        "pdf_serve": False}
        gated = _gate_articles(articles)
        return {"source": "arxiv", "available": True,
                "articles": gated,
                "licence_gated_articles": gated,
                "pdf_serve": False,
                "pdf_serve_note": "arXiv PDF serve refused (copyright, "
                                  "not code licence)"}
    if source == "crossref":
        if not doi:
            return {"source": "crossref", "available": False,
                    "reason": "Crossref lookup needs a DOI; "
                              "no DOI supplied, nothing fetched"}
        client = research_stack.CrossrefClient(fetcher=fetcher)
        work = client.fetch_work(doi)
        if isinstance(work, dict) and work.get("available") is False:
            return {**work, "source": "crossref"}
        return {"source": "crossref", "available": True,
                "work": work,
                "crossmark_assertions": client.crossmark(work)
                if isinstance(work, dict) else [],
                "retraction_note": "crossmark assertions only; "
                                   "never an inferred retraction"}
    if source == "openalex":
        client = research_stack.OpenAlexClient(fetcher=fetcher)
        result = client.search(question, daily_cap=daily_cap)
        if isinstance(result, dict) and result.get("available") is False:
            return {**result, "source": "openalex"}
        return {"source": "openalex", "available": True, "result": result}
    return {"source": source, "available": False,
            "reason": f"unknown research source: {source}"}


def research_answer(question: str, docs, limit: int = 3) -> dict:
    """Extractive answer over docs: citations from the log only."""
    answer = research_stack.answer_from_docs(question, docs, limit=limit)
    answer["retrieved_tag"] = research_stack.tag_retrieved(
        [c["passage"] for c in answer["citations"]])
    answer["licence_gated_citations"] = _gate_articles(answer["citations"])
    return answer


def research_plan(question: str) -> dict:
    """Deep-research kernel: a plan, never a run (executed=False)."""
    plan = research_stack.deep_research_plan(question)
    plan["disclosure"] = research_stack.disclosure_flags(False)
    plan["peer_review"] = research_stack.refuse_peer_review()
    return plan


def refuse_peer_review_w6() -> dict:
    return research_stack.refuse_peer_review()


# ---------------------------------------------------------------------------
# Item 7 — PF-3 API surface (verified intact, closes at Wave 9)
# ---------------------------------------------------------------------------

def privacy_api_intact() -> dict:
    """Signature check on the Wave 6 PF-3 API (no caller added here)."""
    expected = {
        "list_stored_facts": ("base", "kinds"),
        "delete_one": ("category", "key", "base"),
        "wipe_all": ("confirm", "base"),
        "export_milestones": ("base",),
    }
    found = {}
    for name in expected:
        fn = getattr(privacy, name, None)
        params = list(inspect.signature(fn).parameters) if fn else []
        found[name] = {"present": fn is not None, "params": params}
    return {
        "intact": all(
            found[name]["present"]
            and all(p in found[name]["params"] for p in expected[name])
            for name in expected),
        "functions": found,
        "closes_at": "Wave 9 PF Privacy Dashboard widget (Wave 9 leaf)",
        "gap": PF3_GAP_OWED,
    }


def pf3_status() -> dict:
    status = privacy_api_intact()
    status["live_today"] = False
    status["reason_not_live"] = (
        "HANDLER-ONLY: only caller is the staged Wave 9 widget "
        "(gui/pf_surfaces.py default_privacy_api); widget wiring "
        "belongs to the Wave 9 leaf.")
    return status


# ---------------------------------------------------------------------------
# Registration-ready tool handlers (dispatch pending Stage 2 landing)
# ---------------------------------------------------------------------------

def knowledge_search_tool(args: dict, db_path=None) -> dict:
    """Tool-shaped wrapper: knowledge_search. args: {query, limit}."""
    args = args or {}
    return knowledge_search(str(args.get("query", "")),
                            limit=int(args.get("limit", 10) or 10),
                            db_path=db_path)


def knowledge_semantic_search_tool(args: dict, db_path=None) -> dict:
    """Tool-shaped wrapper: knowledge_semantic_search.
    args: {query, limit}."""
    args = args or {}
    return knowledge_semantic_search(
        str(args.get("query", "")),
        limit=int(args.get("limit", 10) or 10),
        db_path=db_path)


def knowledge_embed_missing_tool(args: dict, db_path=None) -> dict:
    """Tool-shaped wrapper: knowledge_embed_missing.
    args: {limit}."""
    args = args or {}
    try:
        limit = int(args.get("limit", 50) or 50)
    except (TypeError, ValueError):
        limit = 50
    return knowledge_embed_missing(limit=limit, db_path=db_path)


def knowledge_embedding_status_tool(args: dict, db_path=None) -> dict:
    """Tool-shaped wrapper: knowledge_embedding_status. args: {}."""
    return knowledge_embedding_status(db_path=db_path)


def search_documents_tool(args: dict, db_path=None) -> dict:
    """Tool-shaped wrapper: search_documents. args: {query, limit}."""
    args = args or {}
    try:
        limit = int(args.get("limit", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    return search_documents(str(args.get("query", "")),
                            limit=limit, db_path=db_path)


def list_documents_tool(args: dict, db_path=None) -> dict:
    """Tool-shaped wrapper: list_documents. args: {limit, offset}."""
    args = args or {}
    try:
        limit = int(args.get("limit", 10) or 10)
    except (TypeError, ValueError):
        limit = 10
    try:
        offset = int(args.get("offset", 0) or 0)
    except (TypeError, ValueError):
        offset = 0
    return list_documents(limit=limit, offset=offset, db_path=db_path)


def forget_documents_tool(args: dict, db_path=None) -> dict:
    """Tool-shaped wrapper: forget_documents. args: {source}."""
    args = args or {}
    return forget_documents(str(args.get("source", "")),
                            db_path=db_path)


def research_tool(args: dict, fetcher=None) -> dict:
    """Tool-shaped wrapper: research. args: {question, source, doi}."""
    args = args or {}
    if str(args.get("question", "")).strip().lower().startswith(
            "peer review"):
        return refuse_peer_review_w6()
    return research_entry(
        str(args.get("question", "")),
        source=str(args.get("source", "arxiv")),
        fetcher=fetcher, doi=str(args.get("doi", "")),
        daily_cap=args.get("daily_cap"))


def remember_person_tool(args: dict, base=None) -> dict:
    args = args or {}
    return remember_person(str(args.get("name", "")),
                           relationship=str(args.get("relationship", "")),
                           note=str(args.get("note", "")), base=base)


def remember_promise_tool(args: dict, base=None) -> dict:
    args = args or {}
    return remember_promise(str(args.get("person", "")),
                            str(args.get("promise_text", "")),
                            due_date=str(args.get("due_date", "")),
                            base=base)


def overdue_promises_tool(args: dict, base=None) -> dict:
    args = args or {}
    return {"overdue": overdue_promises_w6(
        today=args.get("today") or None, base=base)}


W6_TOOL_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "knowledge_search": knowledge_search_tool,
    "semantic_search_knowledge": knowledge_semantic_search_tool,
    "embed_knowledge": knowledge_embed_missing_tool,
    "knowledge_embedding_status": knowledge_embedding_status_tool,
    # Darwin D1: the document-memory tools ride the same wiring.
    "search_documents": search_documents_tool,
    "list_documents": list_documents_tool,
    "forget_documents": forget_documents_tool,
    "research": research_tool,
    "remember_person": remember_person_tool,
    "remember_promise": remember_promise_tool,
    "overdue_promises": overdue_promises_tool,
}

REGISTRATION_STEP = (
    "After Stage 2 lands: (1) measure each new declaration's "
    "bytes via the offered-set builders FIRST; "
    "(2) add declarations for knowledge_search, research, "
    "remember_person, remember_promise, overdue_promises to "
    "core/funnel/tools_handlers.py ONLY if the offered-set total stays "
    "<= 240,000 B, else overflow-queue the remainder; "
    "(3) dispatch each to the matching W6_TOOL_HANDLERS "
    "function in this module; (4) re-run test_conv_w6.py + "
    "the offered-set budget check."
)
