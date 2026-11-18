"""research_edition_deltas_loop — part of the research_edition_deltas split (Phase B B10).

Moved verbatim from core/memory/research_edition_deltas.py; see the
facade there for the re-export map.
"""
from .research_edition_deltas_helpers import _unavailable  # INTENTIONAL (research_edition_deltas split B10): shared helper, lives in helpers per plan §2 P3(c)
from .research_stack import summarise_without_executor
from .research_stack import tag_retrieved

# ---------------------------------------------------------------------------
# RS19-A1/A5/A6 — deep-research loop kernel (pick: A6 pattern)
# ---------------------------------------------------------------------------
# Pick ONE kernel: A6 (Wave 6 already pilots the A6 pattern in
# deep_research_plan; picking A5 as well would double-build).
# A5 is recorded in the studies file as the non-picked
# alternative. The kernel is a PURE state machine: retrieve is
# an injected callable, summarise reuses Wave 6's
# summarise_without_executor, and NOTHING here touches an
# executor — a suite AST-asserts that. Rounds are capped;
# citations come ONLY from the retrieval log.



MAX_RESEARCH_ROUNDS = 3


def research_loop(question, retrieve, max_rounds=MAX_RESEARCH_ROUNDS):
    """Run the A6-pattern loop: retrieve -> tag (data-only) ->
    summarise (no executor). retrieve(query) returns a list of
    doc dicts (doc_id/title/passage/source/licence). The loop
    NEVER executes retrieved content and NEVER calls tools.
    Returns the retrieval log, the summary, and citations drawn
    from the log alone."""
    if retrieve is None:
        return _unavailable(
            "no retrieve function injected: the loop plans but "
            "never runs without a caller-supplied retriever")
    rounds = min(int(max_rounds or 1), MAX_RESEARCH_ROUNDS)
    log = []
    query = str(question or "")
    rounds_run = 0
    for _ in range(rounds):
        rounds_run += 1
        docs = retrieve(query) or []
        for doc in docs:
            tagged = tag_retrieved(doc)
            entry = dict(doc)
            entry["tagged"] = tagged["kind"]
            entry["executable"] = False
            log.append(entry)
        if not docs:
            break
    summary = summarise_without_executor(
        " ".join(str(entry.get("passage", ""))
                 for entry in log), log)
    return {
        "question": question,
        "kernel": "A6-pattern (local-only)",
        "rounds_run": rounds_run,
        "retrieval_log": log,
        "summary": summary["summary"],
        "citations_from_log": summary["citations_from_log"],
        "executor_access": False,
        "excluded": "A5 self-hosted Firecrawl option (AGPL)",
    }
