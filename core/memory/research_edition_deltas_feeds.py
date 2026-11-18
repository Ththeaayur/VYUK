"""research_edition_deltas_feeds — part of the research_edition_deltas split (Phase B B10).

Moved verbatim from core/memory/research_edition_deltas.py; see the
facade there for the re-export map.
"""
from ..config import DATA_DIR
import xml.etree.ElementTree as ET
from .research_edition_deltas_helpers import _unavailable  # INTENTIONAL (research_edition_deltas split B10): shared helper, lives in helpers per plan §2 P3(c)
import json
import os
import re

# ---------------------------------------------------------------------------
# RS19-B1 — arXiv firehose: RSS + OAI-PMH parsers, URL builders
# ---------------------------------------------------------------------------
# Wave 6's ArxivClient covers the API + Atom parse. RSS 2.0 and
# OAI-PMH ListRecords are the net-new shapes. Same record shape
# as parse_atom (id/title/summary/authors) plus pub_date where
# the feed carries one. Metadata/digest ONLY — PDF serve stays
# refused in Wave 6 and is not re-offered here.



def rss_url(category):
    """arXiv RSS feed URL for a category (e.g. 'cs.AI').
    URL construction only — the caller fetches it."""
    return f"https://rss.arxiv.org/rss/{category}"


def oai_url(set_spec=None, from_date=None, until_date=None):
    """arXiv OAI-PMH ListRecords URL. Parameters are request
    construction, never a send."""
    params = ["verb=ListRecords",
              "metadataPrefix=arXiv"]
    if set_spec:
        params.append(f"set={set_spec}")
    if from_date:
        params.append(f"from={from_date}")
    if until_date:
        params.append(f"until={until_date}")
    return "https://export.arxiv.org/oai2?" + "&".join(params)


def _clean(text):
    return " ".join((text or "").split())


def parse_rss(feed_xml):
    """Parse an RSS 2.0 feed into metadata records. Pure
    parsing — no network, no PDFs. Unparseable input yields []."""
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return []
    records = []
    for item in root.iter("item"):
        title = item.findtext("title", "")
        summary = item.findtext("description", "")
        link = item.findtext("link", "")
        guid = item.findtext("guid", "")
        authors = [author.text for author in item.findall("author")
                   if author.text]
        creator = item.findtext(
            "{http://purl.org/dc/elements/1.1/}creator", "")
        if creator:
            authors.append(creator)
        records.append({
            "id": _clean(guid or link),
            "title": _clean(title),
            "summary": _clean(summary),
            "authors": authors,
            "pub_date": _clean(item.findtext("pubDate", "")),
        })
    return records


def parse_oai_pmh(feed_xml):
    """Parse an OAI-PMH ListRecords response (oai_dc records)
    into metadata records. Pure parsing — no network, no PDFs.
    Namespace-robust: dc fields are matched by namespace URI,
    not by prefix (prefixes vary across servers)."""
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError:
        return []
    dc_ns = "{http://purl.org/dc/elements/1.1/}"
    oai_ns = "{http://www.openarchives.org/OAI/2.0/}"
    records = []
    for record in root.iter(oai_ns + "record"):
        identifier = ""
        for header in record.iter(oai_ns + "header"):
            for ident in header.iter(oai_ns + "identifier"):
                identifier = ident.text or ""
                break
            break
        fields = {}
        for elem in record.iter():
            if elem.tag.startswith(dc_ns):
                tag = elem.tag[len(dc_ns):]
                fields.setdefault(tag, []).append(elem.text or "")
        records.append({
            "id": _clean(identifier
                         or (fields.get("identifier") or [""])[0]),
            "title": _clean(" ".join(fields.get("title", []))),
            "summary": _clean(" ".join(fields.get("description", []))),
            "authors": fields.get("creator", []),
            "pub_date": _clean(" ".join(fields.get("date", []))),
        })
    return records


def ratings_from_store(store, doc_ids):
    """Bridge KN-4 ratings into a plain mapping for rank_items.
    store is a KnowledgeStore; missing ratings are simply
    absent (never zero-filled — absence is not a score)."""
    ratings = {}
    for doc_id in doc_ids or []:
        row = store.get_rating(doc_id)
        if row is not None:
            ratings[doc_id] = row["rating"]
    return ratings


def _rank_tokens(text):
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def rank_items(items, query, ratings=None):
    """Native triage ranking (arxiv-sanity-style, reimplemented):
    token overlap with title+summary, plus a bounded recency
    bonus, plus a KN-4 rating bonus. Deterministic: ties break
    by id, so the same input always ranks the same way.
    Ratings are integers (KN-4's rule); non-integers are
    ignored, never coerced. Scores are on-device only."""
    query_tokens = _rank_tokens(query)
    ratings = ratings or {}
    scored = []
    for item in items or []:
        haystack = _rank_tokens(
            f"{item.get('title', '')} {item.get('summary', '')}")
        overlap = (len(query_tokens & haystack) / len(query_tokens)
                   if query_tokens else 0.0)
        # Recency bonus: at most +0.10, only when pub_date is a
        # parseable ISO date; undated items get no bonus.
        recency = 0.0
        pub = str(item.get("pub_date", "") or "")[:10]
        if re.match(r"\d{4}-\d{2}-\d{2}$", pub):
            recency = 0.10 if pub >= "2024-01-01" else 0.05
        rating = ratings.get(item.get("id", ""))
        rating_bonus = 0.0
        if isinstance(rating, int) and not isinstance(rating, bool):
            rating_bonus = max(-0.20, min(0.20, rating * 0.05))
        score = round(overlap + recency + rating_bonus, 4)
        scored.append((score, str(item.get("id", "")), item))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [{"score": score, "item": item}
            for score, _id, item in scored]


FEED_POLL_STATE_FILENAME = "feed_poll_state.json"


class FeedPollState:
    """Miniflux-pattern poll state: per-feed seen ids, persisted
    as JSON under DATA_DIR. Dedup key is guid, else link, else
    id — never the title (titles repeat)."""

    def __init__(self, state_path=None):
        self.state_path = state_path or os.path.join(
            str(DATA_DIR), FEED_POLL_STATE_FILENAME)

    def _load(self):
        try:
            with open(self.state_path, "r",
                      encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, UnicodeDecodeError):
            return {}

    def _save(self, data):
        directory = os.path.dirname(self.state_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.state_path, "w",
                  encoding="utf-8") as handle:
            json.dump(data, handle, indent=1, sort_keys=True)

    def seen_ids(self, feed_url):
        return set(self._load().get(feed_url, []))

    def mark_seen(self, feed_url, ids):
        data = self._load()
        merged = set(data.get(feed_url, [])) | set(ids or [])
        data[feed_url] = sorted(merged)
        self._save(data)
        return data[feed_url]


def poll_feed(feed_url, fetcher, state=None, parser=None):
    """Poll ONE feed through an injected fetcher, parse it
    (RSS or Atom by root tag when no parser is given), and
    return ONLY items not seen before. The seen-set updates
    only after a successful parse — a failed fetch never marks
    items seen. With no fetcher, returns unavailable."""
    if fetcher is None:
        return _unavailable(
            "no fetcher injected: feed request built but never "
            "sent (caller-funnelled egress)")
    state = state or FeedPollState()
    raw = fetcher(feed_url)
    if isinstance(raw, dict) and not raw.get("available", True):
        return raw
    xml_text = raw if isinstance(raw, str) else str(raw)
    if parser is not None:
        records = parser(xml_text)
    elif "<rss" in xml_text[:2000]:
        records = parse_rss(xml_text)
    else:
        from .research_stack import ArxivClient
        records = ArxivClient.parse_atom(xml_text)
    seen = state.seen_ids(feed_url)
    fresh = []
    for record in records:
        key = record.get("id", "")
        if key and key not in seen:
            fresh.append(record)
    state.mark_seen(feed_url, [item.get("id", "")
                               for item in records if item.get("id")])
    return {"available": True, "feed": feed_url,
            "new_items": fresh, "seen_total": len(seen)}
