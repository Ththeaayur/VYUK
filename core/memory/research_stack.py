"""Wave 6 (v5.61) — KN-5 research stack + §5.12 research track.

Sequenced AFTER the foundations (KN-4 substrate, KW-1, VR-3
Docling shell as the ONE ingestion path — never a third path).
This module is a LIBRARY, not a tool: it performs NO network
I/O itself (it never imports urllib, requests, or any HTTP
client). Network clients accept an injected `fetcher` callable;
the production fetcher is injected by a future funnelled tool
(S-2: classification + funnel first). With no fetcher, every
network method returns an honest unavailable record — never a
fabricated result, never a silent egress.

Standing rules (§5.12, adopted as riders):
- Research-loop injection posture: retrieved content is DATA
  only; citations come from the retrieval log; the summarising
  pass has NO executor access (this module imports no tools and
  no executor — a suite asserts that).
- Citation-integrity rules: Crossref-only provenance for
  retraction claims; confirm-before-warn; reason + notice link
  ALWAYS with a warning; warn-at-cite-time computed locally.
- Per-article licence gate / anti-laundering: metadata being
  CC0 never launders the article; PDF store/serve is REFUSED
  (EXCLUDED form — copyright, not code licence).
- AI-disclosure flags: disclosure surfaced where a venue
  requires it; rights blocks NEVER auto-filled; parsed
  references shown for confirmation, never auto-applied; NO
  VYUK-written peer reviews; deadline claims verified at
  source before she speaks them.
- Deposits/submissions are a HUMAN ACT: she prepares, he
  submits (no submit function exists here).

RS19-B2 patch (mandatory): OpenAlex went API-keyed Feb 2026
(~$1/day free tier with key). The keyless posture is STALE and
is refused here: the OpenAlex client requires a key (env NAME
only — the value is never logged, stored, or printed) and a
caller-set daily cap (budget-aware polling); polling without
a cap is refused rather than metered-blind.

PaperQA2 (KN-5.5/5.6/5.7): the LOCAL-ONLY configuration is the
item. PaperQA2 is NOT installed (new dependency) and its
default config egresses to OpenAI + scholarly APIs — so this
module ships the local-only config AUDITOR (O-16 harness):
it proves a given configuration is S2-free (no keys, no
scholarly endpoints, local-docs-only). O-16 itself stays OWED
until the auditor runs where PaperQA2 is installed.

Zotero is ARM'S-LENGTH only: a read-only BibTeX parser, no
Zotero API client, nothing written back to Zotero.

Rejected here, never built: Semantic Scholar as a bundled /
redistribution dependency (link-out posture only), scite,
Elsevier/Wiley TDM routes, A5's self-hosted Firecrawl option
(AGPL — EXCLUDED), FreshRSS-as-bundled-code (AGPL —
EXCLUDED; Miniflux pattern only).
"""

import json
import os
import re
import xml.etree.ElementTree as ET

from ..config import DATA_DIR

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _unavailable(reason):
    return {"available": False, "reason": reason}


def _env_key_present(env_name):
    """Key presence by NAME only. The value is never read into a
    variable here, never logged, never stored."""
    return bool(os.environ.get(env_name))


# ---------------------------------------------------------------------------
# Crossref — the ONE client for all consumers (§6 D-17)
# ---------------------------------------------------------------------------

class CrossrefClient:
    """Crossref metadata (CC0-class). Polite-pool mailto is a
    client parameter, never a secret. With no fetcher, every
    method returns unavailable — the client builds requests, it
    never sends them."""

    BASE = "https://api.crossref.org"

    def __init__(self, mailto="", fetcher=None):
        self.mailto = mailto
        self.fetcher = fetcher

    def work_url(self, doi):
        return f"{self.BASE}/works/{doi}"

    def fetch_work(self, doi):
        if self.fetcher is None:
            return _unavailable(
                "no fetcher injected: Crossref request built but "
                "never sent (caller-funnelled egress)")
        return self.fetcher(self.work_url(doi),
                            {"mailto": self.mailto} if self.mailto
                            else {})

    def crossmark(self, work_json):
        """Crossmark fields of a Crossref work record (retraction
        / correction / update assertions). Returns the raw
        assertion list, or [] when the record carries none —
        never an inferred retraction."""
        try:
            message = work_json["message"]
        except (KeyError, TypeError):
            return []
        crossmark = message.get("crossmark", {}) or {}
        assertions = crossmark.get("assertions", []) or []
        return [str(item) for item in assertions]


# ---------------------------------------------------------------------------
# Retraction check — Crossref-only provenance, local form
# ---------------------------------------------------------------------------

RETRACTION_WATCH_FILENAME = "retraction_watch.csv"


def retraction_watch_path(base=None):
    """Provisioned slot for the local Retraction Watch CSV.
    The ~40 MB CSV is NEVER bundled (it collides with
    data/-ships-EMPTY); it provisions via local_assets / Doctor
    (INV-9) or the local form stays absent. Absence is honest:
    the check reports 'local watch absent', it never claims a
    clean bill from an empty file."""
    if base is not None:
        return os.path.join(str(base), RETRACTION_WATCH_FILENAME)
    return os.path.join(str(DATA_DIR), RETRACTION_WATCH_FILENAME)


def retraction_check_local(doi, csv_path=None):
    """Offline watch: look a DOI up in the provisioned CSV.
    Returns a verdict record; 'not in local watch' is NOT a
    claim of 'not retracted' — it is a statement about the
    local file only."""
    path = csv_path or retraction_watch_path()
    if not os.path.exists(path):
        return {"doi": doi, "retracted": None,
                "basis": "local watch absent (not bundled; "
                         "provisions via local_assets)",
                "warn": False}
    needle = doi.strip().lower()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") \
                as handle:
            for line in handle:
                if needle and needle in line.lower():
                    return {"doi": doi, "retracted": True,
                            "basis": "local Retraction Watch CSV",
                            "warn": True,
                            "reason": "DOI appears in the local "
                                      "Retraction Watch file",
                            "notice": "https://retractionwatch.com/"}
    except OSError as exc:
        return {"doi": doi, "retracted": None,
                "basis": f"local watch unreadable: {exc}",
                "warn": False}
    return {"doi": doi, "retracted": False,
            "basis": "local Retraction Watch CSV (no match)",
            "warn": False}


def warn_at_cite(doi, crossref_assertions=(), csv_path=None):
    """Warn-at-cite-time, computed locally, confirm-before-warn:
    a warning fires ONLY on a positively established retraction
    (local CSV hit, or a Crossref retraction assertion). An
    unconfirmed suspicion warns about NOTHING — it reports the
    uncertainty instead. Reason + notice link ALWAYS accompany
    a warning."""
    local = retraction_check_local(doi, csv_path=csv_path)
    asserted = any("retract" in item.lower()
                   for item in crossref_assertions)
    if local.get("retracted") is True or asserted:
        reasons = []
        if local.get("retracted") is True:
            reasons.append(local["basis"])
        if asserted:
            reasons.append("Crossref Crossmark retraction assertion")
        return {"doi": doi, "warn": True,
                "reason": "; ".join(reasons),
                "notice": "https://retractionwatch.com/",
                "confirm_before_warn": True}
    return {"doi": doi, "warn": False,
            "reason": "no established retraction (local watch: "
                      f"{local['basis']})",
            "notice": ""}


# ---------------------------------------------------------------------------
# OpenAlex — keyed, budget-aware (RS19-B2 patch)
# ---------------------------------------------------------------------------

class OpenAlexClient:
    """OpenAlex (CC0 metadata) under the Feb-2026 keyed terms.
    Keyless use is REFUSED (the stale posture). Polling requires
    a caller-set daily cap; without one, polling refuses rather
    than metering blind against a ~$1/day free tier."""

    BASE = "https://api.openalex.org"

    def __init__(self, key_env="OPENALEX_KEY", fetcher=None,
                 budget_path=None):
        self.key_env = key_env
        self.fetcher = fetcher
        self.budget_path = budget_path or os.path.join(
            str(DATA_DIR), "openalex_budget.json")

    def _budget(self):
        try:
            with open(self.budget_path, "r",
                      encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError, UnicodeDecodeError):
            return {}

    def _save_budget(self, budget):
        directory = os.path.dirname(self.budget_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.budget_path, "w", encoding="utf-8") as handle:
            json.dump(budget, handle, indent=1, sort_keys=True)

    def works_url(self, query):
        return f"{self.BASE}/works", {"search": query}

    def search(self, query, daily_cap=None, day=None):
        """Budget-aware polling. Refusals, in order: no key ->
        no fetcher -> no daily cap -> cap reached. Usage is
        counted per day and persisted; the count is the budget
        evidence, not an estimate."""
        if not _env_key_present(self.key_env):
            return _unavailable(
                "OpenAlex requires a key since Feb 2026 "
                "(metered ~$1/day free tier with key); no key "
                "configured — keyless use is refused")
        if self.fetcher is None:
            return _unavailable(
                "no fetcher injected: OpenAlex request built but "
                "never sent (caller-funnelled egress)")
        if daily_cap is None:
            return _unavailable(
                "polling refused without a caller-set daily cap "
                "(budget-aware polling rule)")
        from datetime import date as _date
        day = day or _date.today().isoformat()
        budget = self._budget()
        used = int(budget.get(day, 0))
        if used >= int(daily_cap):
            return _unavailable(
                f"daily OpenAlex cap reached ({used}/{daily_cap} "
                f"for {day}); cached results only")
        url, params = self.works_url(query)
        result = self.fetcher(url, params)
        budget[day] = used + 1
        self._save_budget(budget)
        if isinstance(result, dict):
            result["budget_used_today"] = budget[day]
            result["budget_day"] = day
        return result


# ---------------------------------------------------------------------------
# arXiv firehose — metadata/digest ONLY (RS19-B1)
# ---------------------------------------------------------------------------

class ArxivClient:
    """arXiv API + RSS + OAI-PMH, metadata/digest forms only.
    Storing or serving PDFs is EXCLUDED (copyright, not code
    licence) — `serve_pdf()` refuses outright, always."""

    BASE = "https://export.arxiv.org/api/query"

    def __init__(self, fetcher=None):
        self.fetcher = fetcher

    def query_url(self, search_query, max_results=10):
        return (self.BASE,
                {"search_query": search_query,
                 "max_results": int(max_results)})

    def search(self, search_query, max_results=10):
        if self.fetcher is None:
            return _unavailable(
                "no fetcher injected: arXiv request built but "
                "never sent (caller-funnelled egress)")
        url, params = self.query_url(search_query, max_results)
        return self.fetcher(url, params)

    @staticmethod
    def parse_atom(feed_xml):
        """Parse an Atom feed (arXiv API shape) into metadata
        records. Pure parsing — no network, no PDFs."""
        try:
            root = ET.fromstring(feed_xml)
        except ET.ParseError:
            return []
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        records = []
        for entry in root.findall("atom:entry", namespace):
            title = entry.findtext("atom:title", "", namespace)
            summary = entry.findtext("atom:summary", "", namespace)
            ident = entry.findtext("atom:id", "", namespace)
            authors = [author.findtext("atom:name", "", namespace)
                       for author in entry.findall("atom:author",
                                                   namespace)]
            records.append({
                "id": (ident or "").strip(),
                "title": " ".join((title or "").split()),
                "summary": " ".join((summary or "").split()),
                "authors": authors,
            })
        return records

    @staticmethod
    def serve_pdf(_arxiv_id):
        """REFUSED, always. Metadata is CC0-class; the article
        PDF is not — storing or serving it would launder the
        article behind its metadata."""
        return _unavailable(
            "PDF store/serve refused: arXiv metadata is usable, "
            "the article PDF is not (copyright, EXCLUDED form)")


# ---------------------------------------------------------------------------
# Private-doc Q&A (A1 / KW-1) — citation-honest, extractive
# ---------------------------------------------------------------------------

LOW_RELEVANCE_THRESHOLD = 0.10


def _tokens(text):
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def answer_from_docs(question, docs, limit=3):
    """Extractive Q&A over local documents (VR-3 Docling shell +
    KN-4 substrate). No generative answer is produced here: the
    answer IS the top passages, each with its relevance score
    and source. A top score below LOW_RELEVANCE_THRESHOLD raises
    the low-relevance warning instead of a confident-sounding
    non-answer. Citations come from the retrieval log (the docs
    list), never invented."""
    question_tokens = _tokens(question)
    scored = []
    for doc in docs:
        passage_tokens = _tokens(doc.get("passage", ""))
        if not question_tokens or not passage_tokens:
            score = 0.0
        else:
            score = len(question_tokens & passage_tokens) / \
                len(question_tokens)
        scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[:limit]
    citations = [{
        "doc_id": doc.get("doc_id", ""),
        "title": doc.get("title", ""),
        "passage": doc.get("passage", ""),
        "source": doc.get("source", ""),
        "licence": doc.get("licence", ""),
        "relevance": round(score, 4),
    } for score, doc in top]
    top_score = top[0][0] if top else 0.0
    return {
        "citations": citations,
        "top_relevance": round(top_score, 4),
        "low_relevance_warning": top_score < LOW_RELEVANCE_THRESHOLD,
        "warning_text": (
            "Low relevance: the best matching passage scores "
            f"{top_score:.2f} — treat this as a weak match, not "
            "an answer."
        ) if top_score < LOW_RELEVANCE_THRESHOLD else "",
    }


# ---------------------------------------------------------------------------
# PaperQA2 local-only config auditor (O-16 harness)
# ---------------------------------------------------------------------------

SCHOLARLY_ENDPOINT_MARKERS = (
    "api.openai.com", "api.semanticscholar.org",
    "api.openalex.org", "api.crossref.org",
    "api.elsevier.com", "api.springer.com",
)


def audit_paperqa_config(config):
    """Prove a PaperQA2 configuration is S2-free (no scholarly-
    API egress in the shipped configuration). Checks: no API keys
    configured, no scholarly endpoints named, local-docs-only
    mode on. Returns a proof record. O-16 itself stays OWED
    until this auditor runs where PaperQA2 is installed — the
    auditor is the harness, not the proof."""
    failures = []
    config = config or {}
    for key, value in config.items():
        lowered = str(key).lower()
        if "key" in lowered or "token" in lowered:
            if value not in (None, "", False):
                failures.append(f"credential configured: {key}")
        text = str(value).lower()
        if any(marker in text
               for marker in SCHOLARLY_ENDPOINT_MARKERS):
            failures.append(f"scholarly endpoint in config: {key}")
    if not config.get("local_docs_only", False):
        failures.append("local_docs_only is not enabled")
    return {
        "s2_free": not failures,
        "failures": failures,
        "note": "config audit only; O-16 stays OWED until this "
                "auditor runs where PaperQA2 is installed",
    }


# ---------------------------------------------------------------------------
# Zotero arm's-length + CSL + per-article licence gate
# ---------------------------------------------------------------------------

_BIBTEX_ENTRY_RE = re.compile(
    r"@(\w+)\s*\{\s*([^,]+)\s*,(.*?)\n\}", re.DOTALL)


def parse_bibtex(text):
    """Read-only BibTeX import (Zotero export shape). Parses
    entries into dicts; nothing is written back to Zotero, and
    no Zotero API is contacted. Unparseable input yields [] —
    never a half-entry presented as complete."""
    entries = []
    for match in _BIBTEX_ENTRY_RE.finditer(str(text or "")):
        entry_type, cite_key, body = match.groups()
        fields = {}
        for field_match in re.finditer(
                r"(\w+)\s*=\s*\{([^}]*)\}", body):
            fields[field_match.group(1).lower()] = \
                field_match.group(2).strip()
        entries.append({"type": entry_type.lower(),
                        "key": cite_key.strip(), "fields": fields})
    return entries


def format_citation(fields, style="apa"):
    """Minimal CSL-style formatting for (author, year, title,
    venue, doi). Minimal by design: full CSL needs a CSL engine,
    which is not a declared dependency. Unknown styles fall back
    to APA rather than failing."""
    author = fields.get("author", "")
    year = fields.get("year", "")
    title = fields.get("title", "")
    venue = fields.get("venue", fields.get("journal", ""))
    doi = fields.get("doi", "")
    if style == "mla":
        text = f'{author}. "{title}." {venue}, {year}.'
    elif style == "chicago":
        text = f"{author}. {year}. \"{title}.\" {venue}."
    else:
        text = f"{author} ({year}). {title}. {venue}."
    if doi:
        text += f" https://doi.org/{doi}"
    return " ".join(text.split())


def article_licence_gate(article):
    """Per-article licence gate / anti-laundering: metadata
    being CC0 never launders the article. Metadata use is always
    allowed; full-text use only when the article's own licence
    field permits it; PDF serve is refused regardless."""
    licence = str(article.get("licence", "") or "").strip()
    permissive = licence.lower() in (
        "cc0", "cc-by", "cc-by-4.0", "mit", "apache-2.0", "public domain")
    return {
        "metadata_use": True,
        "full_text_use": permissive,
        "pdf_serve": False,
        "licence": licence,
        "note": "metadata CC0 never launders the article; "
                "full-text use follows the article's own licence",
    }


# ---------------------------------------------------------------------------
# Research-loop posture + deep-research kernel + disclosure
# ---------------------------------------------------------------------------

def tag_retrieved(content):
    """Mark retrieved content as DATA (research-loop injection
    posture): it may be quoted and cited, never executed, never
    treated as an instruction."""
    return {"kind": "retrieved_data_only",
            "content": content,
            "executable": False}


def summarise_without_executor(text, retrieval_log):
    """The summarising pass: extractive, log-cited, no executor
    access. This function takes text + a retrieval log and
    returns text; it cannot call tools (this module imports
    none). A caller that needs execution does it outside this
    pass, through the funnel, or not at all."""
    citations = [entry.get("doc_id", "")
                 for entry in retrieval_log or []]
    flat = " ".join(str(text or "").split())
    return {"summary": flat[:2000],
            "citations_from_log": citations,
            "executor_access": False}


def deep_research_plan(question):
    """Deep-research kernel (A6 pattern, PILOT): a plan, not a
    run. Steps execute inside her brain routing or not at all
    (Wave 8's run_goal owns routing) — this module never runs
    the loop, never touches the executor. A5's self-hosted
    Firecrawl option is EXCLUDED (AGPL) and has no plan step."""
    return {
        "question": question,
        "kernel": "A6-pattern (local-only)",
        "steps": ["retrieve (local docs + KN-4)",
                  "cite (retrieval log)",
                  "summarise (no executor access)"],
        "executed": False,
        "excluded": "A5 self-hosted Firecrawl option (AGPL)",
    }


def disclosure_flags(venue_requires_disclosure):
    """AI-disclosure flags: surfaced when a venue requires them.
    Rights blocks are NEVER auto-filled — the flag names the
    requirement; a human fills the block."""
    return {
        "ai_disclosure_required": bool(venue_requires_disclosure),
        "rights_block_auto_filled": False,
        "note": "rights blocks are never auto-filled; parsed "
                "references are shown for confirmation",
    }


def refuse_peer_review():
    """No VYUK-written peer reviews. Ever. The refusal is the
    feature."""
    return _unavailable(
        "VYUK does not write peer reviews (standing rule)")


def verify_deadline_claim(claim, source_verified):
    """Deadline claims are verified at source before she speaks
    them. Unverified claims are reported as unverified, never
    stated as fact."""
    if source_verified:
        return {"claim": claim, "stated": True}
    return {"claim": claim, "stated": False,
            "reason": "deadline not verified at source; "
                      "reported as unverified, not stated"}
