"""PULSE feeds, Wave 5 (stdlib only, no network, no bundled store).

Attribution and terms rules, enforced in code rather than prose:
* Wikidata entity cards: CC0, primary-verified. A card carries its
  source; a card without a source is refused.
* Wikipedia live summaries: CC BY-SA — attributed extracts plus a
  link-out ONLY. A bundled store of Wikipedia content is refused
  outright (``store_summary`` always refuses).
* NewsData.io ambient news: primary terms read OWED, so no live
  NewsData layer is allowed until that read completes; and ambient
  means a delay of at least 12 hours — anything fresher is refused
  as breaking news, which this feed is not.
* Currents headlines: spoken AI summaries need separate terms.
  That restriction bites directly for a voice assistant, so spoken
  summaries on Currents are refused until those terms clear. The
  restriction is carried as a flag, never worked around.
"""

from __future__ import annotations

from dataclasses import dataclass, field

AMBIENT_DELAY_HOURS = 12


@dataclass
class PulseItem:
    """One PULSE item, as supplied by the caller (no fetching here)."""
    kind: str  # "wikidata" | "wikipedia" | "newsdata" | "currents"
    title: str
    body: str = ""
    source: str = ""
    link: str = ""
    delay_hours: float | None = None
    attribution: str = ""


def wikidata_card(item: PulseItem) -> dict:
    """Build a Wikidata entity card (CC0). Source is mandatory."""
    if item.kind != "wikidata":
        return {"ok": False, "reason": "not a wikidata item"}
    if not item.source:
        return {"ok": False, "reason": "wikidata card without a source refused"}
    return {"ok": True, "card": {"title": item.title, "body": item.body,
                                 "source": item.source, "licence": "CC0"}}


def wikipedia_summary(item: PulseItem) -> dict:
    """An attributed Wikipedia extract plus link-out, never a store."""
    if item.kind != "wikipedia":
        return {"ok": False, "reason": "not a wikipedia item"}
    if not item.link:
        return {"ok": False, "reason": "wikipedia summary without a link-out refused"}
    attribution = item.attribution or "Wikipedia, CC BY-SA (attributed extract)"
    return {"ok": True, "summary": {"title": item.title, "body": item.body,
                                    "link": item.link,
                                    "attribution": attribution}}


def store_summary(item: PulseItem) -> dict:
    """Refused, always: Wikipedia content is never bundled into a store."""
    return {"ok": False,
            "reason": "bundled Wikipedia store refused: CC BY-SA extracts "
                      "are attributed extracts plus link-out only"}


def newsdata_item(item: PulseItem, terms_read: bool = False) -> dict:
    """Ambient NewsData item. Terms OWED blocks live use; delay < 12h
    blocks the item as breaking news, which this feed is not."""
    if item.kind != "newsdata":
        return {"ok": False, "reason": "not a newsdata item"}
    if not terms_read:
        return {"ok": False,
                "reason": "NewsData primary terms read OWED: no live layer"}
    if item.delay_hours is None or item.delay_hours < AMBIENT_DELAY_HOURS:
        return {"ok": False,
                "reason": f"ambient news needs a {AMBIENT_DELAY_HOURS}h delay; "
                          "fresher items are breaking news, not ambient"}
    return {"ok": True, "item": {"title": item.title, "body": item.body,
                                 "source": item.source,
                                 "delay_hours": item.delay_hours}}


def currents_spoken_summary(item: PulseItem, terms_read: bool = False) -> dict:
    """Currents spoken AI summary: refused until separate terms clear.

    This is the restriction that bites for a voice assistant: text
    headlines are one thing, a SPOKEN AI summary is a separate terms
    question, and the answer is no until that read completes.
    """
    if item.kind != "currents":
        return {"ok": False, "reason": "not a currents item"}
    if not terms_read:
        return {"ok": False,
                "reason": "Currents spoken-AI-summary terms OWED: "
                          "spoken summary refused"}
    return {"ok": True, "summary": {"title": item.title, "body": item.body,
                                    "source": item.source}}
