"""
Web skills V for VYUK (v5.21, HANDS — Web II pack): read a
feed's headlines, unshorten a link by narrating the sniff walk,
watch a page for changes, inspect a response's headers, geolocate
an IP, and look up a page's archived snapshots.

Same rules as the other packs: plain functions returning
human-readable strings; handlers never raise and never fake a
result. Every fetch goes through the proxy-aware opener from
core/senses (on campus/hostel networks the direct route can be
dead while the browser — and the proxy — work), with timeouts of
at most 10 seconds. Caps (items shown, bytes read) are disclosed
in the result text or the descriptions.

Read/write split: rss_digest, link_unshorten, http_headers,
ip_geolocate and wayback_lookup only look (READ-ONLY);
page_watch keeps a small state file (hash, title, length and
timestamp per watched URL — never page bodies).

Deliberately NOT here: reachability is website_status, the deep
link inspection is sniff_link (link_unshorten narrates it), TLS
details are ssl_cert_info and bandwidth is speed_test.

Wiring: the parent (core/funnel/tools_registry.py) calls register_all(registry) — this
module never touches the registry singleton itself.
"""

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

from . import senses
from .config import DATA_DIR

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 VYUK/5.21")
_TIMEOUT = 10                 # seconds, per the tool descriptions
_FEED_CAP = 512 * 1024        # feed bytes read at most
_PAGE_CAP = 512 * 1024        # watched-page bytes read at most
_WATCH_FILE = "page_watch.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _opener():
    """The proxy-aware opener from core/senses (its sniff policy)."""
    return senses._build_sniff_opener()


def _normalise_url(raw: str) -> tuple[str, str]:
    """(full_url, host). A bare domain gets https://; anything
    without a usable host comes back with an empty host."""
    text = (raw or "").strip()
    if not text:
        return "", ""
    full = text if "://" in text else f"https://{text}"
    host = urllib.parse.urlsplit(full).hostname or ""
    return full, host


def _fetch(url: str, cap: int) -> tuple[bytes | None, object, str]:
    """GET through the proxy-aware opener. Returns (body, response,
    error_line): body is None on failure, with error_line saying why
    in plain words; on success error_line is ''."""
    req = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with _opener().open(req, timeout=_TIMEOUT) as resp:
            return resp.read(cap), resp, ""
    except urllib.error.HTTPError as e:
        try:
            body = e.read(cap)
        except Exception:  # noqa: BLE001 — the status is the news
            body = b""
        return body, e, ""
    except Exception as e:  # noqa: BLE001 — classified plainly by callers
        return None, None, f"the fetch failed: {e}"


# ---------------------------------------------------------------------------
# RSS DIGEST — a feed's headlines (read-only)
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """An ElementTree tag without its {namespace} prefix."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(elem, name: str) -> str:
    for child in elem:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _atom_link(elem) -> str:
    """An Atom entry's link: rel=alternate wins, else the first href."""
    fallback = ""
    for child in elem:
        if _local(child.tag) != "link":
            continue
        href = child.get("href") or ""
        if not href:
            continue
        if child.get("rel") in (None, "alternate"):
            return href
        if not fallback:
            fallback = href
    return fallback


def _parse_feed(root) -> tuple[str, list[dict]] | None:
    """(feed_title, [{title, link, date}]) for RSS 2.0 or Atom
    roots; None when the document is neither."""
    kind = _local(root.tag)
    if kind == "rss":
        channel = None
        for child in root:
            if _local(child.tag) == "channel":
                channel = child
                break
        if channel is None:
            return None
        items = []
        for entry in channel:
            if _local(entry.tag) != "item":
                continue
            items.append({
                "title": _child_text(entry, "title") or "(untitled)",
                "link": _child_text(entry, "link"),
                "date": _child_text(entry, "pubDate"),
            })
        return _child_text(channel, "title"), items
    if kind == "feed":  # Atom
        items = []
        for entry in root:
            if _local(entry.tag) != "entry":
                continue
            date = (_child_text(entry, "published")
                    or _child_text(entry, "updated"))
            items.append({
                "title": _child_text(entry, "title") or "(untitled)",
                "link": _atom_link(entry),
                "date": date,
            })
        return _child_text(root, "title"), items
    return None


def rss_digest(url: str, count: int = 5, filter: str = "") -> str:
    """The top headlines from an RSS 2.0 or Atom feed.

    With `filter`, only items whose title contains the text
    (case-insensitively) are digested — a busy feed can carry a
    hundred items, and the count still applies after filtering."""
    try:
        n = int(count)
    except (TypeError, ValueError):
        return f"'{count}' isn't a number of headlines."
    n = max(1, min(25, n))
    needle = (filter or "").strip().lower()
    full, host = _normalise_url(url)
    if not host:
        return ("Give me a feed address — e.g. "
                "rss_digest('https://example.com/feed.xml').")
    body, _resp, err = _fetch(full, _FEED_CAP)
    if body is None:
        return f"Couldn't fetch the feed at {full} — {err}."
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return (f"{host} answered, but what came back isn't an RSS "
                "or Atom feed I can read (it may be a web page — a "
                "feed address usually ends in /feed, .rss or .xml).")
    parsed = _parse_feed(root)
    if parsed is None:
        return (f"{host} answered with well-formed XML, but it "
                "isn't an RSS or Atom feed — nothing to digest.")
    title, items = parsed
    if not items:
        return (f"The feed '{title or host}' is reachable but has "
                "no items in it right now.")
    total = len(items)
    if needle:
        items = [i for i in items if needle in i["title"].lower()]
        if not items:
            return (f"The feed '{title or host}' has {total} item(s), "
                    f"but none has '{filter}' in its title right now.")
    shown = items[:n]
    if needle:
        lines = [f"{title or host} — {len(shown)} item(s) with "
                 f"'{filter}' in the title (of {total} in the feed):"]
    else:
        lines = [f"{title or host} — the latest {len(shown)} of "
                 f"{len(items)} item(s) in the feed:"]
    for item in shown:
        line = f"• {item['title']}"
        if item["date"]:
            line += f" ({item['date']})"
        lines.append(line)
        if item["link"]:
            lines.append(f"  {item['link']}")
    if len(items) > len(shown):
        lines.append(f"…and {len(items) - len(shown)} more — ask "
                     "for a bigger count to see them.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LINK UNSHORTEN — the sniff walk, narrated (read-only)
# ---------------------------------------------------------------------------


def link_unshorten(url: str) -> str:
    """Follow a short link's redirects and name the real destination."""
    try:
        report = senses.sniff_link(url)
    except Exception as e:  # noqa: BLE001
        return f"I couldn't follow that link: {e}"
    if not report.get("ok"):
        return report.get("verdict", "I couldn't check that link.")
    hops = report.get("hops", [])
    redirects = max(0, len(hops) - 1)
    start = (url or "").strip()
    final = report.get("final_url", "")
    lines = []
    if redirects:
        chain = [h["url"] for h in hops]
        chain.append(final)
        lines.append(f"{start} leads, after {redirects} "
                     f"redirect(s), to {final}.")
        lines.append("The chain: " + " → ".join(chain))
    else:
        lines.append(f"{start} doesn't redirect — it answers "
                     f"directly at {final}.")
    details = []
    if report.get("status"):
        details.append(f"final answer HTTP {report['status']}")
    if report.get("shortener"):
        details.append("it started at a known link shortener, "
                       "which is why the destination was hidden")
    if details:
        lines.append("(" + "; ".join(details) + ".)")
    lines.append("For the full inspection — content type, server "
                 "and certificate expiry — ask for a sniff of the "
                 "link instead.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# PAGE WATCH — notice when a page changes (stores a small state file)
# ---------------------------------------------------------------------------


def _watch_path():
    return DATA_DIR / _WATCH_FILE


def _load_watches() -> dict:
    try:
        data = json.loads(_watch_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — missing/corrupt state = no watches
        return {}


def _save_watches(watches: dict) -> bool:
    try:
        _watch_path().write_text(json.dumps(watches, indent=2),
                                 encoding="utf-8")
        return True
    except OSError:
        return False


def _page_title(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    m = re.search(r"<title[^>]*>(.*?)</title>", text,
                  re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return " ".join(m.group(1).split())[:120]


def page_watch(url: str, action: str = "check") -> str:
    """Watch a page: baseline on first check, change reports after."""
    act = (action or "check").strip().lower()
    if act not in ("check", "forget", "list"):
        return (f"Unknown page_watch action: '{action}'. Use "
                "'check' (watch / compare), 'forget' (stop "
                "watching) or 'list' (show the watches).")
    if act == "list":
        watches = _load_watches()
        if not watches:
            return ("You're not watching any pages right now — "
                    "use page_watch with action 'check' on a page "
                    "to start one.")
        lines = [f"Watching {len(watches)} page(s):"]
        for link in sorted(watches)[:50]:
            entry = watches[link] or {}
            bits = [link]
            if entry.get("title"):
                bits.append(f"'{entry['title']}'")
            if entry.get("checked_at"):
                bits.append(f"baseline {entry['checked_at']}")
            if entry.get("length") is not None:
                bits.append(f"{entry['length']:,} bytes")
            lines.append("• " + " — ".join(bits))
        if len(watches) > 50:
            lines.append(f"…and {len(watches) - 50} more.")
        return "\n".join(lines)
    full, host = _normalise_url(url)
    if not host:
        return "Give me a page address to watch."
    watches = _load_watches()
    if act == "forget":
        if full in watches:
            del watches[full]
            if not _save_watches(watches):
                return ("I forgot the watch on this side, but "
                        "couldn't save the watch list — it may come "
                        "back next start.")
            return f"Stopped watching {full}."
        return f"{full} wasn't being watched."
    body, _resp, err = _fetch(full, _PAGE_CAP)
    if body is None:
        return (f"Couldn't check {full} — {err}. (The watch "
                "baseline, if any, is unchanged.)")
    digest = hashlib.sha256(body).hexdigest()
    title = _page_title(body)
    stamp = datetime.now().isoformat(timespec="seconds")
    previous = watches.get(full)
    watches[full] = {"sha256": digest, "title": title,
                     "length": len(body), "checked_at": stamp}
    if not _save_watches(watches):
        return ("I checked the page but couldn't save the watch "
                "state, so the comparison won't stick.")
    if previous is None:
        return (f"Now watching {full}"
                + (f" — '{title}'" if title else "")
                + f", {len(body):,} bytes. Ask me to check it "
                  "again any time and I'll tell you whether it "
                  "changed. (Only a fingerprint is kept — never the "
                  "page itself.)")
    if previous.get("sha256") == digest:
        return (f"No change to {full} since "
                f"{previous.get('checked_at', 'the last check')} — "
                f"still {len(body):,} bytes"
                + (f", titled '{title}'" if title else "") + ".")
    lines = [f"{full} CHANGED since "
             f"{previous.get('checked_at', 'the last check')}:"]
    old_title = previous.get("title", "")
    if old_title != title:
        lines.append(f"• Title: '{old_title or '(none)'}' → "
                     f"'{title or '(none)'}'")
    lines.append(f"• Size: {previous.get('length', 0):,} → "
                 f"{len(body):,} bytes")
    lines.append("The new version is the baseline now.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTTP HEADERS — the response's paperwork (read-only)
# ---------------------------------------------------------------------------

_SECURITY_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "X-Frame-Options",
    "X-Content-Type-Options",
)


def http_headers(url: str, all_headers: bool = False) -> str:
    """A page's response headers, with the security set checked off.

    all_headers=true appends every header the server sent (the
    summary above stays the same), values truncated for speech."""
    if isinstance(all_headers, str):
        want_all = all_headers.strip().lower() in ("true", "1", "yes")
    else:
        want_all = bool(all_headers)
    full, host = _normalise_url(url)
    if not host:
        return ("Give me a page address — e.g. "
                "http_headers('example.com').")
    req = urllib.request.Request(
        full, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with _opener().open(req, timeout=_TIMEOUT) as resp:
            status = resp.getcode() or 0
            headers = resp.headers
            final = resp.geturl() or full
    except urllib.error.HTTPError as e:
        status = e.code or 0
        headers = getattr(e, "headers", None) or getattr(e, "hdrs", None)
        final = e.geturl() or full
    except Exception as e:  # noqa: BLE001
        return f"Couldn't fetch {full} — the fetch failed: {e}."
    if headers is None:
        return f"{host} answered {status} but sent no headers to read."
    lines = [f"{final} answered HTTP {status}."]
    for label, header in (("Server", "Server"),
                          ("Content type", "Content-Type"),
                          ("Content length", "Content-Length"),
                          ("Cache control", "Cache-Control")):
        value = headers.get(header)
        if value:
            lines.append(f"• {label}: {value}")
    present = [h for h in _SECURITY_HEADERS if headers.get(h)]
    missing = [h for h in _SECURITY_HEADERS if not headers.get(h)]
    if present:
        lines.append("Security headers in place: "
                     + ", ".join(present) + ".")
    if missing:
        lines.append("Security headers missing: "
                     + ", ".join(missing) + " — common gaps, worth "
                     "knowing about, not proof a site is unsafe.")
    if not present:
        lines.append("None of the four standard security headers "
                     "are set — the site's paperwork is thin.")
    if want_all:
        pairs = []
        for key, value in headers.items():
            text = " ".join(str(value).split())
            if len(text) > 200:
                text = text[:200] + "…"
            pairs.append(f"• {key}: {text}")
        lines.append(f"All response headers ({len(pairs)}):")
        lines.extend(pairs[:40])
        if len(pairs) > 40:
            lines.append(f"…and {len(pairs) - 40} more header(s).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# IP GEOLOCATE — where an address lives, roughly (read-only)
# ---------------------------------------------------------------------------


def ip_geolocate(ip: str = "") -> str:
    """Country/city/ISP for an IP, via the free ip-api.com service."""
    target = (ip or "").strip()
    endpoint = ("http://ip-api.com/json/"
                + urllib.parse.quote(target, safe=""))
    body, _resp, err = _fetch(endpoint, 64 * 1024)
    if body is None:
        return (f"Couldn't look up {target or 'your public IP'} — "
                f"{err}. (The lookup uses the free ip-api.com "
                "service; it may be unreachable or rate-limiting.)")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return "The geolocation service answered something I couldn't read."
    if data.get("status") != "success":
        return (f"The geolocation service couldn't place "
                f"{target or 'that address'}: "
                f"{data.get('message', 'unknown reason')}. (Private "
                "and reserved addresses have no public location.)")
    who = data.get("query") or target or "Your public IP"
    where = ", ".join(x for x in (data.get("city"),
                                  data.get("regionName"),
                                  data.get("country")) if x) or "unknown"
    lines = [f"{who} is in {where}."]
    if data.get("isp"):
        lines.append(f"Provider: {data['isp']}"
                     + (f" ({data['org']})" if data.get("org")
                        and data.get("org") != data["isp"] else "")
                     + ".")
    if data.get("timezone"):
        lines.append(f"Local timezone there: {data['timezone']}.")
    if data.get("as"):
        lines.append(f"Network: {data['as']}.")
    flags = []
    if data.get("proxy"):
        flags.append("a proxy or VPN exit")
    if data.get("hosting"):
        flags.append("a hosting/datacentre address")
    if data.get("mobile"):
        flags.append("a mobile network")
    if flags:
        lines.append("The service flags this address as "
                     + ", ".join(flags) + ".")
    lines.append("City-level at best — IP locations are approximate "
                 "and a VPN moves the answer to the VPN's doorstep.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# WAYBACK LOOKUP — a page's archived snapshots (read-only)
# ---------------------------------------------------------------------------


def wayback_lookup(url: str) -> str:
    """The closest Internet Archive snapshot of a page, if one exists."""
    full, host = _normalise_url(url)
    if not host:
        return "Give me a page address to look up in the archive."
    endpoint = ("https://archive.org/wayback/available?url="
                + urllib.parse.quote(full, safe=""))
    body, _resp, err = _fetch(endpoint, 64 * 1024)
    if body is None:
        return (f"Couldn't reach the Internet Archive — {err}.")
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return "The Internet Archive answered something I couldn't read."
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not closest.get("available") or not closest.get("url"):
        return (f"The Internet Archive has no snapshots of {full} "
                "— it was never archived, or the archive doesn't "
                "know it under that address.")
    stamp = str(closest.get("timestamp", ""))
    when = stamp
    try:
        when = datetime.strptime(stamp, "%Y%m%d%H%M%S").strftime(
            "%d %B %Y, %H:%M")
    except ValueError:
        pass
    return (f"Closest archived snapshot of {full}: taken "
            f"{when}.\n{closest['url']}\n(That's the nearest capture "
            "the Internet Archive holds — the page may have changed "
            "many times between then and now.)")


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (core/funnel/tools_registry.py wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Web II pack tool on a ToolRegistry-like object."""
    registry.register(
        name="rss_digest",
        description="Read an RSS 2.0 or Atom feed and digest its newest headlines: the feed's title, then the top items (5 by default, up to 25) with each item's title, date when the feed gives one, and link. An optional filter narrows the digest to items with that word in the title. Fetched through the system proxy with at most 512 KB read. Use when the user asks what's new on a blog, news site or podcast feed, or pastes a feed address. If the address is a web page rather than a feed it says so honestly. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The feed address, e.g. 'https://example.com/feed.xml' (a bare domain gets https://)"},
                "count": {"type": "integer",
                          "description": "How many items to show, 1–25 (default 5)"},
                "filter": {"type": "string",
                           "description": "Optional: only digest items whose title contains this text (case-insensitive)"},
            },
            "required": ["url"],
        },
        handler=rss_digest,
    )
    registry.register(
        name="link_unshorten",
        description="Reveal where a shortened or redirecting link really leads: follows its redirect chain (up to 8 hops) through the system proxy and reports the final destination and how many redirects it took, with the chain spelled out. Use when the user pastes a bit.ly / tinyurl / t.co-style link or asks 'where does this link go?'. Only http/https. For the deeper inspection (content type, server, certificate expiry) use sniff_link instead. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The shortened or suspect link, e.g. 'https://bit.ly/3AbCdEf'"},
            },
            "required": ["url"],
        },
        handler=link_unshorten,
    )
    registry.register(
        name="page_watch",
        description="Watch a web page for changes. The first 'check' saves a baseline — a SHA-256 fingerprint, the page title and its size in bytes (never the page body) — and later checks compare: 'no change since …' or a change report with the old and new title and size. The new version becomes the baseline after a change. action='forget' stops watching one page; action='list' shows every watched page with its baseline (the url can be empty for list). Use when the user asks to keep an eye on a page, a price, an announcement or a results page. State lives in VYUK's data folder; fetches cap at 512 KB through the system proxy.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The page to watch, e.g. 'https://example.com/results' (can be empty when action is 'list')"},
                "action": {"type": "string",
                           "enum": ["check", "forget", "list"],
                           "description": "check (default) = baseline or compare; forget = stop watching this page; list = show all watches"},
            },
            "required": ["url"],
        },
        handler=page_watch,
    )
    registry.register(
        name="http_headers",
        description="Read a web page's response headers — its paperwork: the HTTP status, server software, content type and length, cache policy, and a checklist of the four standard security headers (Strict-Transport-Security, Content-Security-Policy, X-Frame-Options, X-Content-Type-Options) noting which are present and which are missing. all_headers=true also lists every header the server sent. Only headers are read, never the page body. Use when the user asks how a site is configured or why a browser is strict with it. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The page to inspect, e.g. 'example.com' or a full URL"},
                "all_headers": {"type": "boolean",
                                "description": "Also list every response header (default false)"},
            },
            "required": ["url"],
        },
        handler=http_headers,
    )
    registry.register(
        name="ip_geolocate",
        description="Look up where an IP address is located, roughly: country, region, city, internet provider and timezone, via the free ip-api.com service (an external lookup — the address asked about is sent to that service). Also reports the network (AS) and flags the service attaches — proxy/VPN exit, hosting address, mobile network. Leave the IP empty to locate this PC's own public address. City-level at best, and a VPN moves the answer. (public_ip already includes this summary for this PC's own address.) Use when the user asks where an address, a server or a suspicious connection is based. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "ip": {"type": "string",
                       "description": "The IP address to locate (optional; empty = this PC's public IP)"},
            },
            "required": [],
        },
        handler=ip_geolocate,
    )
    registry.register(
        name="wayback_lookup",
        description="Find the closest snapshot of a web page in the Internet Archive (the Wayback Machine): when the nearest capture was taken and its archive URL, or an honest 'no snapshots' when the page was never archived. Use when the user asks what a page used to say, wants an old version of a page, or a page has vanished. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The page to look up, e.g. 'https://example.com/old-page'"},
            },
            "required": ["url"],
        },
        handler=wayback_lookup,
    )
