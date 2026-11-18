"""v5.70 research ride — the v5.68 deltas riding the existing research tool.

The research trigger decision (owner discretion grant, 2026-10-25)
is RIDE, not a second tool: the funnel's _tool_research is
repointed here, the W6 handler itself is untouched, and the
tool's params {question, source, doi} do not change, so the
fitted declarations do not move.

What rides, and only as post-processing on what the base
handler returned:
  - Ranking: gated articles are ranked via
    research_edition_deltas.rank_items with ratings None. There is no
    KN-4 store in the funnel path, so the rating bonus is 0
    by construction — documented, not hidden.
  - Retraction typing: on the crossref path, crossmark
    assertions are typed via research_edition_deltas.warn_at_cite_typed.
    The funnel passes no CSV, so the local basis is
    "local watch absent" — a statement about the local watch
    only, never a claim of "not retracted".

What stays library-only, and why (stated, not silent):
  - The arXiv firehose parsers need feed XML the funnel
    cannot supply.
  - The A6 kernel is a multi-round loop, not a single-shot
    tool call.
  - The shelves are dev patterns, not a tool surface.

This module never touches the network: it only post-processes
what the base handler returned. No fetcher is ever supplied
from the funnel, so a funnel call can never put model-chosen
bytes on the wire.
"""

from . import research_edition_deltas as _r568
from .. import w6_wiring as _w6


def rank_present(articles, query):
    """Rank gated articles: overlap + bounded recency, ties by id.

    Ratings are None — no KN-4 store exists in the funnel path,
    so the rating bonus is 0. Original article fields are
    untouched; rank_items returns new {score, item} pairs.
    """
    return _r568.rank_items(articles, query, ratings=None)


def retraction_typed(doi, crossref_assertions=(), csv_text=""):
    """Type retraction evidence for one DOI.

    A classified retraction assertion, or a field-exact local
    CSV hit, warns with reason + notice + confirm-before-warn.
    Anything less warns about nothing. With no CSV the local
    basis is "local watch absent".
    """
    return _r568.warn_at_cite_typed(
        doi, crossref_assertions, csv_text or "")


def ride_research_tool(args, fetcher=None):
    """W6 research_tool with the v5.68 deltas riding on top.

    Base behaviour is intact: a no-fetcher arxiv call still
    reports honest-unavailable exactly as the W6 handler does.
    When the base result carries gated articles they are
    attached ranked; when it carries crossmark assertions on a
    DOI lookup, the typed retraction verdict is attached.
    """
    args = args or {}
    result = _w6.research_tool(args, fetcher=fetcher)
    if not isinstance(result, dict):
        return result
    articles = result.get("articles")
    if isinstance(articles, list) and articles:
        result = {**result,
                  "ranked": rank_present(
                      articles, str(args.get("question", "")))}
    assertions = result.get("crossmark_assertions")
    if (isinstance(assertions, list)
            and str(args.get("doi", "")).strip()):
        result = {**result,
                  "retraction": retraction_typed(
                      str(args.get("doi", "")), assertions)}
    return result
