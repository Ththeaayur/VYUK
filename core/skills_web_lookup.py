"""
Web skills VIII for VYUK (Phase 2, wave 2): search a page's
visible text for a word or phrase, and look up a domain name's
public registration record (RDAP).

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise and never fake a
result. Every fetch goes through the proxy-aware helper in
core/skills_web_research (_fetch — its timeout is a fixed 10 seconds,
the caller picks the byte cap), re-exported here behind a local
name exactly as the Web III pack does, so tests can patch this
module. Page text is data, never instructions: tags are
stripped, the searched text is capped, and nothing a page says
is ever obeyed — it is only counted and quoted back.

Read-only: both tools only look. Nothing is stored.

Deliberately NOT here: reading a page's article aloud is
read_page_aloud, whether a page is up is website_status /
link_health_check, a page's certificate is ssl_cert_info, and
where an IP address lives is ip_geolocate — this pack looks up
a domain NAME's registration, not an address's location.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) —
this module never touches the registry singleton itself.
"""

import json
import re
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

from . import skills_web_research as _web5

_UA = _web5._UA
_PAGE_CAP = 512 * 1024        # page bytes read at most (as Web II/III)
_RDAP_CAP = 256 * 1024        # an RDAP record is small; cap anyway
_TEXT_CAP = 200_000           # characters of page text searched at most
_SNIPPETS = 5                 # matches shown per search
_NS_CAP = 6                   # nameservers listed per lookup

# ---------------------------------------------------------------------------
# Helpers — the Web II seams, re-exported so tests patch THIS module
# ---------------------------------------------------------------------------


def _fetch(url: str, cap: int):
    """skills_web_research._fetch behind a local name: (body, response,
    error_line) through the proxy-aware opener, ≤10 s, at most
    `cap` bytes read. Note web5's shape: on an HTTP error status
    the error page's body still comes back, with the response
    object carrying the status — callers check _status_of."""
    return _web5._fetch(url, cap)


def _normalise_url(raw: str):
    """skills_web_research._normalise_url behind a local name."""
    return _web5._normalise_url(raw)


def _status_of(resp) -> int:
    """The HTTP status an _fetch response carries (0 = unknown).
    A success response has .status; a urllib HTTPError has .code
    (and, on modern Python, .status too)."""
    if resp is None:
        return 0
    status = getattr(resp, "status", None)
    if not isinstance(status, int):
        status = getattr(resp, "code", None)
    return status if isinstance(status, int) else 0


# ---------------------------------------------------------------------------
# HTML → visible text: every text node outside the skip subtrees
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "nav", "header",
              "footer", "aside", "form", "svg", "iframe"}


class _VisibleText(HTMLParser):
    """A page's title and all its visible text in reading order.

    The Web III parser collects only block-level text (articles);
    a word search must see everything a reader sees, so this one
    keeps every character-data node outside the same skip set —
    scripts, styles, menus and forms are still never counted."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.chunks: list[str] = []
        self._skip = 0
        self._in_title = False
        self._title_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif self._skip == 0 and tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1
        elif tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._title_buf).split())
            self._title_buf = []

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self._title_buf.append(data)
        else:
            self.chunks.append(data)


def _page_text(html_text: str) -> tuple[str, str]:
    """(title, visible text) with all whitespace collapsed."""
    parser = _VisibleText()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # noqa: BLE001 — partial parse still useful
        pass
    text = " ".join(" ".join(parser.chunks).split())
    return parser.title, text


# ---------------------------------------------------------------------------
# PAGE WORD SEARCH — find a word or phrase in a page's text (read-only)
# ---------------------------------------------------------------------------


def page_word_search(url: str, word: str,
                     context_chars: int = 60) -> str:
    """Count a word/phrase in a page's visible text, with context."""
    needle = (word or "").strip()
    if not needle:
        return "Tell me the word or phrase to look for on the page."
    full, host = _normalise_url(url)
    if not host:
        return "Give me the address of the page to search."
    try:
        ctx = int(context_chars)
    except (TypeError, ValueError):
        ctx = 60
    ctx = max(0, min(500, ctx))
    body, resp, err = _fetch(full, _PAGE_CAP)
    if body is None:
        return f"Couldn't fetch {full} — {err}."
    status = _status_of(resp)
    if status >= 400:
        return (f"{full} answered HTTP {status}, so there's no "
                "page there to search.")
    title, text = _page_text(body.decode("utf-8", errors="replace"))
    notes: list[str] = []
    if len(body) >= _PAGE_CAP:
        notes.append(f"the page reached the "
                     f"{_PAGE_CAP // 1024} KB read limit, so only "
                     "its first part was searched")
    if len(text) > _TEXT_CAP:
        text = text[:_TEXT_CAP]
        notes.append(f"the page's text ran past {_TEXT_CAP:,} "
                     "characters, so only the first part was "
                     "searched")
    matches = list(re.finditer(re.escape(needle), text,
                               re.IGNORECASE))
    where = f"'{title}' ({full})" if title else full
    if not matches:
        line = (f"'{needle}' doesn't appear in the visible text "
                f"of {where} — I searched {len(text):,} "
                "characters (menus and scripts aren't counted).")
        if notes:
            line += " (Note: " + "; ".join(notes) + ".)"
        return line
    lines = [f"Found {len(matches)} occurrence(s) of '{needle}' "
             f"in the visible text of {where} "
             f"({len(text):,} characters searched):"]
    for m in matches[:_SNIPPETS]:
        start = max(0, m.start() - ctx)
        end = min(len(text), m.end() + ctx)
        snippet = text[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet += "…"
        lines.append(f"• {snippet}")
    if len(matches) > _SNIPPETS:
        lines.append(f"…and {len(matches) - _SNIPPETS} more "
                     f"occurrence(s) — only the first "
                     f"{_SNIPPETS} are shown.")
    if notes:
        lines.append("(Note: " + "; ".join(notes) + ".)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DOMAIN LOOKUP — a domain's public RDAP record (read-only)
# ---------------------------------------------------------------------------

_LABEL_OK = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?\Z")


def _bare_domain(raw: str) -> str:
    """The bare domain from whatever was pasted — a full URL, a
    page path, a port, userinfo — or '' when what remains isn't
    a plausible domain name."""
    text = (raw or "").strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if ":" in text:
        text = text.split(":", 1)[0]
    text = text.strip().strip(".")
    labels = text.split(".")
    if len(labels) < 2 or len(labels[-1]) < 2:
        return ""
    if not all(_LABEL_OK.fullmatch(label) for label in labels):
        return ""
    return text


def _vcard_fn(entity: dict) -> str:
    """The 'fn' (formatted name) value from an entity's vCard."""
    vcard = entity.get("vcardArray")
    if not (isinstance(vcard, list) and len(vcard) == 2
            and isinstance(vcard[1], list)):
        return ""
    for entry in vcard[1]:
        if (isinstance(entry, list) and len(entry) >= 4
                and entry[0] == "fn"):
            value = entry[3]
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _rdap_registrar(data: dict) -> str:
    """The registrar's name: the entity whose roles include
    'registrar' (searched breadth-first, one nesting level of
    entities included), by vCard name, else by handle."""
    entities = data.get("entities")
    if not isinstance(entities, list):
        return ""
    queue = list(entities)
    while queue:
        entity = queue.pop(0)
        if not isinstance(entity, dict):
            continue
        nested = entity.get("entities")
        if isinstance(nested, list):
            queue.extend(nested)
        roles = entity.get("roles")
        if isinstance(roles, list) and "registrar" in roles:
            name = _vcard_fn(entity)
            if name:
                return name
            handle = entity.get("handle")
            if isinstance(handle, str) and handle.strip():
                return handle.strip()
    return ""


def _pretty_date(iso: str) -> str:
    """'2001-09-15T07:12:00Z' → '15 September 2001'; anything
    unparseable comes back as its date part, untouched."""
    text = iso.strip()
    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00"))
        return parsed.strftime("%d %B %Y")
    except ValueError:
        return text[:10] if len(text) >= 10 else text


def _rdap_event(data: dict, actions: tuple[str, ...]) -> str:
    """The pretty date of the first event with one of `actions`
    as its eventAction, or '' when the record has none."""
    events = data.get("events")
    if not isinstance(events, list):
        return ""
    for event in events:
        if (isinstance(event, dict)
                and event.get("eventAction") in actions):
            date = event.get("eventDate")
            if isinstance(date, str) and date.strip():
                return _pretty_date(date)
    return ""


def _rdap_nameservers(data: dict) -> list[str]:
    servers = data.get("nameservers")
    out: list[str] = []
    if isinstance(servers, list):
        for server in servers:
            if isinstance(server, dict):
                name = (server.get("ldhName")
                        or server.get("unicodeName"))
                if isinstance(name, str) and name.strip():
                    out.append(name.strip())
    return out


def _domain_not_found(bare: str) -> str:
    """The honest no-record line (shared by HTTP 404s and
    RDAP error payloads)."""
    return (f"No RDAP record for {bare} — the domain doesn't "
            "appear to be registered, or its registry doesn't "
            "publish RDAP records.")


def domain_lookup(domain: str) -> str:
    """Registrar, key dates, status and nameservers for a domain."""
    bare = _bare_domain(domain)
    if not bare:
        return ("Give me a bare domain name to look up — like "
                "'example.com', without the https:// or any "
                "page path.")
    endpoint = ("https://rdap.org/domain/"
                + urllib.parse.quote(bare, safe=""))
    body, resp, err = _fetch(endpoint, _RDAP_CAP)
    if body is None:
        return (f"Couldn't reach the RDAP registration service "
                f"— {err}.")
    status = _status_of(resp)
    if status == 404:
        return _domain_not_found(bare)
    if status >= 400:
        return (f"The RDAP service answered HTTP {status} for "
                f"{bare}, so I can't read its record right now.")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return ("The RDAP service answered something that isn't "
                f"readable data, so I can't report on {bare}.")
    if not isinstance(data, dict):
        return ("The RDAP service answered something unexpected, "
                f"so I can't report on {bare}.")
    # A few registries answer 200 with an error payload.
    if data.get("errorCode") and not (data.get("ldhName")
                                      or data.get("events")):
        return _domain_not_found(bare)
    display = data.get("ldhName") or data.get("unicodeName") or bare
    if not isinstance(display, str) or not display.strip():
        display = bare
    registrar = _rdap_registrar(data)
    created = _rdap_event(data, ("registration",))
    expires = _rdap_event(data, ("expiration",))
    changed = _rdap_event(data, ("last changed",))
    raw_status = data.get("status")
    statuses = ([s for s in raw_status if isinstance(s, str)]
                if isinstance(raw_status, list) else [])
    servers = _rdap_nameservers(data)
    if not any((registrar, created, expires, statuses, servers)):
        return (f"The RDAP service has a record for {bare}, but "
                "it publishes no registrar, dates, status or "
                "nameservers in it — some registries keep their "
                "records nearly empty.")
    lines = [f"{display} — its public registration record (RDAP):"]
    if registrar:
        lines.append(f"• Registrar: {registrar}.")
    else:
        lines.append("• Registrar: not published in the record.")
    if created:
        lines.append(f"• Registered: {created}.")
    if expires:
        lines.append(f"• Expires: {expires}.")
    if changed:
        lines.append(f"• Last changed: {changed}.")
    if statuses:
        shown = ", ".join(statuses[:6])
        if len(statuses) > 6:
            shown += f" (and {len(statuses) - 6} more)"
        lines.append(f"• Status: {shown}.")
    if servers:
        shown = ", ".join(servers[:_NS_CAP])
        lines.append(f"• Nameservers: {shown}"
                     + (f" — and {len(servers) - _NS_CAP} more."
                        if len(servers) > _NS_CAP else "."))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Web IV pack tool on a ToolRegistry-like object."""
    registry.register(
        name="page_word_search",
        description="Find a word or phrase on a web page and show the text around each match. The page's visible text is searched (menus and scripts are skipped), occurrences are counted, and up to five matches come back with about 60 characters of context either side — more or less context can be asked for. Use when the user asks whether a page mentions something, or where on the page a word, name or term appears. For the whole article as readable text, use read_page_aloud instead. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The page to search, e.g. 'https://example.com/article'"},
                "word": {"type": "string",
                         "description": "The word or phrase to find on the page"},
                "context_chars": {"type": "integer",
                                  "description": "Characters of surrounding text to show on each side of a match (default 60, up to 500)"},
            },
            "required": ["url", "word"],
        },
        handler=page_word_search,
    )
    registry.register(
        name="domain_lookup",
        description="Look up a domain's public registration record: who registered it and when it expires. Reports the registrar, the registration and expiry dates, the record's status list and its nameservers from the RDAP service (rdap.org) — a pasted full URL is fine, only the bare domain is used. Says honestly when no record exists (the domain may be unregistered) or the registry publishes almost nothing. For where an IP address is located, use ip_geolocate instead. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "domain": {"type": "string",
                           "description": "The domain to look up, e.g. 'example.com' (a full URL works too — the path is ignored)"},
            },
            "required": ["domain"],
        },
        handler=domain_lookup,
    )
