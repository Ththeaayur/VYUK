"""MEM non-graph delta plus KNOW/SEARCH with receipts, Wave 5.

Stdlib only, no network, no new schema, no capture.

Rules carried from build-flow §6 Wave 5:
* MEM non-graph delta ONLY, inside the frozen schema. The graph
  form waits on the C-1 schema ruling and is NOT built: any attempt
  to add a graph edge is refused with the C-1 reason, not stored.
* KNOW plus SEARCH deltas carry provenance. The P16 source-finder
  slice returns results WITH receipts (source, link, extract); a
  result without a source is refused, never invented, never cited
  from nothing. No citation is ever fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryNote:
    """A memory note in the existing, frozen shape: title, body,
    and tags only. No new columns, no graph fields."""
    title: str
    body: str = ""
    tags: list = field(default_factory=list)


class NonGraphMemory:
    """A local note store. Deliberately graph-free."""

    def __init__(self):
        self.notes = []

    def add_note(self, note: MemoryNote) -> dict:
        if not note.title:
            return {"ok": False, "reason": "note without a title refused"}
        self.notes.append(note)
        return {"ok": True, "count": len(self.notes)}

    def add_graph_edge(self, _from: str, _to: str, _relation: str) -> dict:
        """Refused, always: the graph form waits on the C-1 ruling."""
        return {"ok": False,
                "reason": "MEM graph form refused: waits on the C-1 "
                          "schema ruling; non-graph notes only"}

    def search(self, query: str) -> list:
        query = query.lower()
        return [n for n in self.notes
                if query in n.title.lower() or query in n.body.lower()]


@dataclass
class SearchResult:
    """A KNOW/SEARCH result with its receipt attached."""
    title: str
    source: str
    link: str = ""
    extract: str = ""


def source_finder(results: list) -> dict:
    """P16 source finder with receipts.

    Accepts dicts or SearchResult objects. A result without a
    source is listed as refused, never returned as a citation and
    never given a plausible-looking source. The caller gets the
    clean results AND the refused list, so nothing is silently
    dropped or silently invented.
    """
    clean, refused = [], []
    for raw in results:
        if isinstance(raw, SearchResult):
            result = raw
        elif isinstance(raw, dict):
            result = SearchResult(title=str(raw.get("title", "")),
                                  source=str(raw.get("source", "")),
                                  link=str(raw.get("link", "")),
                                  extract=str(raw.get("extract", "")))
        else:
            refused.append(f"unrecognised result type: {type(raw).__name__}")
            continue
        if not result.title or not result.source:
            refused.append(f"result without title/source refused: "
                           f"{result.title or '(no title)'}")
            continue
        clean.append(result)
    return {"results": clean, "refused": refused}
