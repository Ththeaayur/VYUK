"""Policy cluster split out of core/tools_gate.py (refactor Phase 1 step 3).
Bodies moved verbatim; behaviour is unchanged. Every name here stays
importable from core.funnel.tools_gate via re-export.
"""

from typing import Any



# PC-B10 curl_cffi read lane (see core/curl_cffi_lane.py).
# Classification: EGRESS, exactly like a native egress read —
# free untainted, gated under taint, and the lane itself
# refuses outright under taint (it has no confirmation popup
# of its own; the shipped-path caller is a handler that
# execute() already gated, and the lane adds the refusal
# rather than removing it). curl_cffi's licence was verified
# MIT from its primary source (GitHub repo page: "License:
# MIT License (MIT)" + README "MIT licensed", 2026-10-25).
# It is LAZY-imported and NEVER a shipped default
# dependency: it is absent from requirements.txt, and the
# lane reports itself unavailable in plain words when the
# package is missing. Bundled-native notices (recorded,
# NOT re-verified from the wheel in this wave — CC-1 must
# read them from the curl_cffi wheel before any shipped
# use of the lane): the wheel bundles libcurl-impersonate
# (a curl fork) and BoringSSL; their licence texts ride
# inside the wheel and must be carried in
# THIRD_PARTY_LICENSES.md at that point, not paraphrased
# here.
CURL_CFFI_LANE_NAME = "curl_cffi_read"
CURL_CFFI_LANE_CLASS = "egress"
CURL_CFFI_IMPERSONATE_ALLOWLIST = frozenset(
    {"chrome", "safari", "edge", "firefox"})
#: The ONLY cookie provenance the lane accepts. Cookies come
#: from HER OWN Playwright context (core/browser.py) — the
#: caller stamps that provenance; the lane never reads a
#: cookie file, never accepts a cookie path, and never
#: accepts cookies from any other source. There is no path
#: argument on the lane at all, so there is nothing to
#: point at a browser profile or a credential store.
CURL_CFFI_COOKIE_SOURCE = "playwright_context"


def curl_cffi_lane_class() -> str:
    """The lane's class: "egress" (see above)."""
    return CURL_CFFI_LANE_CLASS


def curl_cffi_lane_needs_confirmation(tainted: bool) -> bool:
    """Gate predicate for the lane: under taint it needs
    confirmation — and since the lane has no popup, the
    lane treats that as a refusal (core/curl_cffi_lane.py).
    Untainted, the lane is free like any egress read."""
    return bool(tainted)


def playwright_cookies_ok(cookies: Any) -> str | None:
    """Validate Playwright-context cookies (pure, fail-closed).

    Returns None when valid, else a plain-words reason. Each
    cookie must be a dict with string "name" and "value";
    "domain" (if present) must be a string. Cookie VALUES
    are never logged, copied or returned by this check —
    only their shapes are judged. A non-list, a non-dict
    entry, or a missing/empty name is a refusal, not a
    repair: the lane never guesses cookies."""
    if not isinstance(cookies, list):
        return "Cookies must be a list."
    for i, cookie in enumerate(cookies):
        if not isinstance(cookie, dict):
            return f"Cookie {i} is not an object."
        name = cookie.get("name")
        value = cookie.get("value")
        if not isinstance(name, str) or not name:
            return f"Cookie {i} has no name."
        if not isinstance(value, str):
            return f"Cookie {i} has no string value."
        domain = cookie.get("domain")
        if domain is not None and not isinstance(domain, str):
            return f"Cookie {i} has a non-string domain."
    return None
