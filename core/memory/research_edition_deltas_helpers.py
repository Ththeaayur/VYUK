"""research_edition_deltas_helpers — part of the research_edition_deltas split (Phase B B10).

Moved verbatim from core/memory/research_edition_deltas.py; see the
facade there for the re-export map.
"""


def _unavailable(reason):
    return {"available": False, "reason": reason}
