"""Adblock filter matching (R10-4, python-adblock PORT): a read-only
URL matcher over a filter list the caller supplies.

This is a PORT of ArniDagur/python-adblock — Python bindings for
Brave's adblock-rust engine — not an adoption. The binding is
licence-clear (MIT OR Apache-2.0, LICENSE-MIT "Copyright (c) 2020
Árni Dagur Guðmundsson", README: "licensed under either of"
Apache-2.0 / MIT "at your option") and light (win_amd64 wheel
1,326,809 B, zero runtime deps), but it is STALE (last push
2023-03-28, 67 stars) and pins adblock-rust =0.5.6 exactly — a
2023-era engine — as a native extension (the manylinux wheel is
10,302,925 B). A port in own stdlib code is testable in the lab
on both interpreters with synthetic lists, which an adopted
native wheel would not be.

WHAT THIS MODULE IS — and what it is NOT:

* It is the MATCHING engine only: given a URL and a filter list,
  it reports whether a blocking rule matches, whether an
  exception rule allows the URL, or whether nothing matches.
  It is a read-only classifier. It blocks nothing itself.
* It is NOT request interception. The plug point for that would
  be Playwright ``page.route()`` — but the interception wiring
  was never built: the patched engine it would have plugged
  into is gone (removed GAPS-1, 2026-11-12 — the seam and its
  A/B harness are deleted, and the worker drives stock
  Playwright directly). Building interception wiring now would
  be wiring to an engine with no interception — the R9-5
  shape (an enabler with nothing to enable). The interception
  half is STOPPED, not built. If interception is ever wanted,
  the wiring is a separate small job on top of this matcher.

FILTER LISTS — none bundled, none fetched, none guessed:

* NO filter list ships in this tree. The well-known lists cannot
  be bundled: EasyList is dual licensed under GPL-3.0 OR
  Creative Commons Attribution-ShareAlike 3.0 Unported
  (easylist.to licence page) — BOTH are copyleft — and the
  standing commercial rule excludes copyleft material from the
  distributed product. Bundling EasyList (or any list under a
  copyleft / non-commercial / revenue-cap licence) is
  hard-blocked, exactly like a library licence.
* Lists are OPERATOR-SUPPLIED only (the rustic-binary /
  basic-pitch-model pattern): the caller names a list file on
  every call. The module never fetches a list (no network),
  never auto-discovers one, never remembers one, and has no
  default list. A tool with no list file refuses — it never
  invents rules.
* Every answer reports the list file's sha256 + rule counts, so
  a run is reproducible from the list file alone.

SUPPORTED ABP SUBSET (documented exactly — what is not listed
here is NOT supported, and is counted as ignored, never silently
reinterpreted):

* ``||example.com^`` — domain anchor (the host, or any
  subdomain of it, followed by a separator or the end).
* ``|http://example.com/ads/`` — left anchor (match at the
  start of the URL).
* ``example.com/ads/`` — plain substring (match anywhere).
* ``*`` — wildcard (any sequence, including empty).
* ``^`` — separator placeholder (any character that is not a
  letter, digit, ``_``, ``-``, ``.`` or ``%``, or the end of
  the URL).
* ``@@`` prefix — exception (allow): an exception that matches
  beats every blocking rule, whatever the file order.
* ``$domain=a.com|~b.com`` — the request must come from one of
  the listed domains (``~`` excludes); a request with no known
  source domain never matches a ``$domain`` rule.
* ``$third-party`` / ``$~third-party`` — third-party only /
  first-party only; a request with no known source domain never
  matches either.
* ``$script``, ``$image``, ``$stylesheet``,
  ``$xmlhttprequest``, ``$subdocument``, ``$object``, ``$font``,
  ``$media``, ``$other``, ``$document`` — resource types; a
  ``~`` before a type negates it. A request with no stated type
  never matches a type-restricted rule. ``$match-case`` is
  accepted as a no-op (paths are already matched
  case-sensitively, domains case-insensitively, which is the
  ABP default).
* IGNORED, counted, never applied: regex filters (``/.../``),
  element hiding (``##``, ``#@#``, ``#$#``, ``#?#``, ``#%#``),
  and any rule carrying an option outside the supported set
  (``$popup``, ``$csp``, ``$redirect``, ``$badfilter``, ...).
  A rule with an option this module does not understand is
  ignored whole — an option can change what a rule means, so
  guessing would be a false match.

VERDICT RULES (order-independent, stated plainly):

* Blocked iff some blocking rule matches AND no exception rule
  matches. The verdict never depends on file order; only the
  rule REPORTED is the first match in file order.
* Domains are matched case-insensitively; paths are matched
  case-sensitively (the ABP default). The URL fragment (after
  ``#``) is never matched — it is not sent to the server.

TRUST SHAPE:

* Pure local computation on caller-supplied inputs (a URL and a
  list file). No network, no browser, no JavaScript, no
  navigation, no input. The tools are read-only and join
  ``_LOCAL_ONLY_READS`` (the search_indexed_files precedent:
  a local read must sit there or ``_is_egress_read`` would
  misclassify it as egress). The result is NOT an untrusted
  producer — nothing in it comes from a web page — so there is
  no taint entry (the browser_performance pattern).
* The matcher never blocks first-party content by default: it
  blocks nothing at all. A ``$third-party`` rule only matches
  third-party requests; anything that would actually block a
  request is the STOPPED interception half.
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.parse

# No list ships in this tree (see the module docstring: EasyList is
# dual GPL-3.0 OR CC-BY-SA 3.0 — both copyleft — so bundling any
# well-known list is hard-blocked). Lists are operator-supplied
# files only. These caps keep a pathological list file from
# eating the process; EasyList itself is ~3-4 MB / ~70k rules.
_MAX_LIST_BYTES = 20 * 1024 * 1024
_MAX_RULES = 500_000
_MAX_URL_CHARS = 4096

# Resource types this module understands in $options. Anything
# else in $options makes the whole rule ignored (see _parse_rule).
_SUPPORTED_TYPES = frozenset(
    {
        "script",
        "image",
        "stylesheet",
        "xmlhttprequest",
        "subdocument",
        "object",
        "font",
        "media",
        "other",
        "document",
    }
)

# Separator placeholder: any character that is not a letter,
# digit, "_", "-", "." or "%" — or the end of the URL.
_SEP_RE = r"(?:[^a-zA-Z0-9_\-.%]|$)"


class _Rule:
    """One parsed network filter rule."""

    __slots__ = ("raw", "regex", "is_exception", "domains", "third_party", "types", "negated_types")

    def __init__(self, raw, regex, is_exception, domains, third_party, types, negated_types):
        self.raw = raw
        self.regex = regex
        self.is_exception = is_exception
        # domains: None (no $domain) or (allowed frozenset, excluded frozenset)
        self.domains = domains
        # third_party: None (no $third-party option) / True ($third-party) / False ($~third-party)
        self.third_party = third_party
        # types: None (no type restriction) or frozenset of allowed types
        self.types = types
        # negated_types: frozenset of ~type options
        self.negated_types = negated_types

    def matches(self, url: str, host: str, source_domain, resource_type) -> bool:
        """True when this rule matches the request (URL, host,
        source domain, resource type)."""
        if self.regex.search(url) is None:
            return False
        if self.domains is not None:
            if source_domain is None:
                return False
            allowed, excluded = self.domains
            src = source_domain.lower()
            if any(src == d or src.endswith("." + d) for d in excluded):
                return False
            if allowed and not any(src == d or src.endswith("." + d) for d in allowed):
                return False
        if self.third_party is not None:
            if source_domain is None:
                return False
            src = source_domain.lower()
            is_third = not (host == src or host.endswith("." + src))
            if self.third_party is True and not is_third:
                return False
            if self.third_party is False and is_third:
                return False
        if self.types is not None or self.negated_types:
            if resource_type is None:
                return False
            rt = resource_type.lower()
            if self.types is not None and rt not in self.types:
                return False
            if rt in self.negated_types:
                return False
        return True


def _compile_pattern(pattern: str):
    """Compile an ABP network-filter pattern (no @@, no $options)
    to a regex. Returns None when the pattern is empty."""
    if not pattern:
        return None
    out = []
    i = 0
    n = len(pattern)
    # A trailing "|" is a right anchor — trim it first, so the
    # domain scan below never runs past it.
    right_anchor = False
    if pattern.endswith("|") and n > 1:
        right_anchor = True
        n -= 1
    # Domain anchor: ||example.com... — the host, or any subdomain.
    if pattern.startswith("||"):
        out.append(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/]*\.)?")
        i = 2
        # The domain part runs to the first /, *, or ^ — domains
        # are matched case-insensitively, so lowercase it here.
        j = i
        while j < n and pattern[j] not in "/*^":
            j += 1
        out.append(re.escape(pattern[i:j].lower()))
        i = j
    elif pattern.startswith("|"):
        out.append("^")
        i = 1
    if i >= n:
        # Nothing left after the anchors (a bare "|" or "||") —
        # an empty filter, not a rule that matches everything.
        return None
    while i < n:
        ch = pattern[i]
        if ch == "*":
            out.append(".*")
        elif ch == "^":
            out.append(_SEP_RE)
        else:
            out.append(re.escape(ch))
        i += 1
    if right_anchor:
        out.append("$")
    try:
        return re.compile("".join(out))
    except re.error:
        return None


def _parse_rule(line: str):
    """Parse one ABP filter line. Returns (rule, ignored_reason):
    rule is a _Rule, or None when the line is ignored — then
    ignored_reason says why ("regex", "cosmetic", "options",
    "empty"). Comments and headers are handled by the caller."""
    raw = line
    is_exception = False
    if line.startswith("@@"):
        is_exception = True
        line = line[2:]
    # Regex filter: /pattern/ or /pattern/$options — the closing
    # slash is the LAST slash in the line, followed by the end or
    # by "$options". A literal path pattern such as /ads/banner.js
    # has its last slash followed by more path text, so it is not
    # a regex and falls through to the pattern compiler below.
    if line.startswith("/"):
        last_slash = line.rfind("/")
        if last_slash > 0:
            after = line[last_slash + 1:]
            if after == "" or after.startswith("$"):
                return None, "regex"
    # Cosmetic / element hiding — not a network filter.
    for marker in ("##", "#@#", "#$#", "#?#", "#%#"):
        if marker in line:
            return None, "cosmetic"
    # Split off $options at the first "$".
    if "$" in line:
        pattern, options_text = line.split("$", 1)
        options = options_text.split(",") if options_text else []
    else:
        pattern, options = line, []
    domains = None
    third_party = None
    types = None
    negated_types = frozenset()
    negated = set()
    for opt in options:
        opt = opt.strip()
        if not opt:
            continue
        if opt == "match-case":
            continue  # No-op: paths are already case-sensitive.
        if opt == "third-party":
            third_party = True
        elif opt == "~third-party":
            third_party = False
        elif opt.startswith("domain="):
            allowed = set()
            excluded = set()
            for d in opt[len("domain="):].split("|"):
                d = d.strip().lower()
                if not d:
                    continue
                if d.startswith("~"):
                    excluded.add(d[1:])
                else:
                    allowed.add(d)
            domains = (frozenset(allowed), frozenset(excluded))
        elif opt in _SUPPORTED_TYPES:
            types = (types or set()) | {opt}
        elif opt.startswith("~") and opt[1:] in _SUPPORTED_TYPES:
            negated.add(opt[1:])
        else:
            # An option this module does not understand can change
            # what the rule means — ignore the whole rule rather
            # than guess (fail-closed on meaning).
            return None, "options"
    if types is not None:
        types = frozenset(types)
    if negated:
        negated_types = frozenset(negated)
    regex = _compile_pattern(pattern)
    if regex is None:
        return None, "empty"
    return _Rule(raw, regex, is_exception, domains, third_party, types, negated_types), None


class _List:
    """A parsed filter list: rules + provenance."""

    __slots__ = ("sha256", "size", "blocking", "exceptions", "ignored")

    def __init__(self, sha256, size):
        self.sha256 = sha256
        self.size = size
        self.blocking = []
        self.exceptions = []
        # ignored: {"regex": n, "cosmetic": n, "options": n, "empty": n}
        self.ignored = {"regex": 0, "cosmetic": 0, "options": 0, "empty": 0}

    @property
    def ignored_total(self) -> int:
        return sum(self.ignored.values())


def _load_list(list_file: str):
    """Read + parse a filter list file. Returns (_List, error):
    error is a plain-language refusal, or None on success."""
    if not list_file or not isinstance(list_file, str):
        return None, "Give me a filter list file — I ship with no lists and never fetch one."
    if not os.path.isfile(list_file):
        return None, f"No such list file: {list_file}"
    try:
        size = os.path.getsize(list_file)
    except OSError as exc:
        return None, f"Cannot read that list file: {exc}"
    if size > _MAX_LIST_BYTES:
        return None, (
            f"That list file is {size:,} bytes — over the {_MAX_LIST_BYTES:,} byte cap. "
            "Split it or point me at a smaller one."
        )
    try:
        with open(list_file, "rb") as fh:
            raw_bytes = fh.read()
    except OSError as exc:
        return None, f"Cannot read that list file: {exc}"
    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    parsed = _List(sha256, size)
    rule_count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("!") or line.startswith("["):
            continue
        rule_count += 1
        if rule_count > _MAX_RULES:
            return None, (
                f"That list has over {_MAX_RULES:,} rules — over the cap. "
                "Split it or point me at a smaller one."
            )
        rule, ignored_reason = _parse_rule(line)
        if rule is None:
            parsed.ignored[ignored_reason] += 1
        elif rule.is_exception:
            parsed.exceptions.append(rule)
        else:
            parsed.blocking.append(rule)
    return parsed, None


def _split_url(url: str):
    """(match_url, host): the URL with its host lowercased and its
    fragment dropped (the fragment is never sent to the server),
    and the lowercased host. Returns (None, None) when the URL
    has no usable host."""
    try:
        parts = urllib.parse.urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return None, None
        host = (parts.hostname or "").lower()
        if not host:
            return None, None
        try:
            port = parts.port
        except ValueError:
            return None, None
        host_display = "[" + host + "]" if ":" in host else host
        netloc = ""
        if parts.username:
            netloc += parts.username
            if parts.password:
                netloc += ":" + parts.password
            netloc += "@"
        netloc += host_display
        if port:
            netloc += ":" + str(port)
        match_url = urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
        return match_url, host
    except ValueError:
        return None, None


def _list_fingerprint(parsed: _List) -> str:
    return (
        f"list {parsed.sha256[:12]} ({len(parsed.blocking):,} blocking rules, "
        f"{len(parsed.exceptions):,} exceptions, {parsed.ignored_total:,} ignored)"
    )


def check_url_blocked(url=None, list_file=None, source_domain=None, resource_type=None) -> str:
    """Would a filter list block this URL? Give me the address and
    your list file — I tell you which rule matches, or that an
    exception rule allows it, or that nothing matches. I ship
    with no lists and never fetch one; the list file is yours.
    Read-only, local-only."""
    if not url or not isinstance(url, str):
        return "Give me a URL to check."
    if len(url) > _MAX_URL_CHARS:
        return f"That URL is {len(url):,} characters — over the {_MAX_URL_CHARS:,} cap."
    parsed, error = _load_list(list_file)
    if error is not None:
        return error
    match_url, host = _split_url(url)
    if match_url is None:
        return f"That is not a URL I can check (I need a scheme and a host): {url}"
    src = source_domain.lower() if isinstance(source_domain, str) and source_domain else None
    rt = resource_type.lower() if isinstance(resource_type, str) and resource_type else None
    for rule in parsed.exceptions:
        if rule.matches(match_url, host, src, rt):
            return (
                f"Allowed by exception rule '{rule.raw}' — it matches {url}, "
                f"so the blocking rules do not apply ({_list_fingerprint(parsed)})."
            )
    for rule in parsed.blocking:
        if rule.matches(match_url, host, src, rt):
            return (
                f"Blocked by rule '{rule.raw}' ({_list_fingerprint(parsed)}). "
                "This is only the match — nothing was blocked in a browser: "
                "request interception is not built (the browser engine it "
                "would plug into ships OFF pending its live A/B win)."
            )
    ignored = f" {parsed.ignored_total:,} rules in the list were ignored (regex / element hiding / options I do not understand)." if parsed.ignored_total else ""
    return (
        f"Not blocked: no rule matched {url} ({_list_fingerprint(parsed)}).{ignored}"
    )


def filter_list_status(list_file=None) -> str:
    """Check a filter list file you supply: how many blocking and
    exception rules it has, which rules I cannot use (regular
    expression filters, element hiding, options I do not
    understand), and its fingerprint so you can tell lists apart.
    I ship with no lists and never fetch one. Read-only,
    local-only."""
    parsed, error = _load_list(list_file)
    if error is not None:
        return error
    ignored = parsed.ignored
    detail = (
        f"ignored — regex {ignored['regex']:,}, element hiding {ignored['cosmetic']:,}, "
        f"unsupported options {ignored['options']:,}, empty {ignored['empty']:,}"
    )
    return (
        f"Filter list {list_file}: sha256 {parsed.sha256}, {parsed.size:,} bytes, "
        f"{len(parsed.blocking):,} blocking rules, {len(parsed.exceptions):,} exception rules, "
        f"{parsed.ignored_total:,} ignored ({detail}). "
        "I use only the ABP subset in core/desktop/browser_adblock.py — "
        "domain anchors, substrings, wildcards, separators, exceptions, "
        "$domain, $third-party and resource types."
    )


def register_all(registry) -> None:
    """Register the python-adblock pack (R10-4, python-adblock
    port): check_url_blocked / filter_list_status — two read-only
    tools over a filter list the caller supplies. Own stdlib ABP
    subset matcher; nothing adopted, no list bundled, no list
    fetched. The module owns its catalogue via register_all
    (same pattern as the other packs)."""
    registry.register(
        name="check_url_blocked",
        description="Check whether a web address would be blocked by a filter list you supply: give me the address and your list file, and I'll tell you which rule matches — or that an exception rule allows it, or that nothing matches. I never fetch lists and I ship with none; the list file is yours. Read-only, local-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string",
                        "description": "The web address to check, e.g. https://ads.example.com/banner.js"},
                "list_file": {"type": "string",
                              "description": "Your filter list file (ABP syntax, e.g. an EasyList-format text file). Required — I ship with no lists and never fetch one."},
                "source_domain": {"type": "string",
                                  "description": "The domain of the page making the request, e.g. example.com — needed for $domain and $third-party rules; without it those rules never match."},
                "resource_type": {"type": "string",
                                  "description": "What kind of request it is: script, image, stylesheet, xmlhttprequest, subdocument, object, font, media, other or document — needed for type-restricted rules; without it those rules never match."},
            },
            "required": ["url", "list_file"],
        },
        handler=check_url_blocked,
    )
    registry.register(
        name="filter_list_status",
        description="Check a filter list file you supply: how many blocking and exception rules it has, which rules I cannot use (regular-expression filters, element hiding, options I do not understand), and its fingerprint so you can tell lists apart. I never fetch lists and I ship with none. Read-only, local-only.",
        parameters={
            "type": "object",
            "properties": {
                "list_file": {"type": "string",
                              "description": "Your filter list file (ABP syntax). Required — I ship with no lists and never fetch one."},
            },
            "required": ["list_file"],
        },
        handler=filter_list_status,
    )
