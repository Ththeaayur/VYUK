"""curl_cffi read lane, classified + gated BEFORE it exists (v5.59,
Wave 4 PC-B10, safeguard S-2: classification + funnel first).

What this lane is: a read-only HTTP GET helper that
impersonates a browser's TLS/HTTP2 fingerprint (curl_cffi)
for pages her Playwright browser already visits — the
"read lane" for content that needs a browser fingerprint
but not a browser window. What it is NOT: a shipped
default, a registry tool, or a bypass. It adds no registry
tool, grows no fitted declaration, and is imported by no
shipped default path (the shipped tree does not import
this module; tests import it directly).

CLASSIFICATION (core/funnel/tools_gate.py, added before this
module): "egress" — free untainted, gated under taint.
Under taint the lane REFUSES outright: it has no
confirmation popup of its own, and a tainted read lane
is exactly the exfiltration shape the egress class
exists to stop. In the shipped path, the lane's only
caller must be a handler that tools.execute already
dispatched and gated; this module's own taint refusal
is the second lock, not a replacement for the funnel.

COOKIES (load-bearing): ONLY her own Playwright
context's cookies. The caller passes the cookie list
plus the provenance stamp ``cookie_source=
"playwright_context"``; anything else is refused. There
is deliberately NO path argument anywhere in this
module — no cookie file, no profile directory, no
credential store can be pointed at, because there is
nothing to point. Cookie values are never logged,
never written to disk, and never appear in any error
string (errors name counts and shapes, never values).
Only HTTPS URLs are fetched; only GET is offered
(no POST body, no form submission, no credential
submission). Password fields and CAPTCHAs are
permanent walls: this lane fetches pages, it never
fills, submits or solves anything.

LICENCE (primary source, 2026-10-25): curl_cffi is MIT
(GitHub repo page: "License: MIT License (MIT)" +
README "MIT licensed"). It is LAZY-imported inside
``_default_transport`` only — absent from
requirements.txt, never imported at module import, and
when the package is absent the lane reports itself
unavailable in plain words instead of failing the
import. BUNDLED-NATIVE NOTICES (recorded, NOT
re-verified from the wheel in this wave): the curl_cffi
wheel bundles libcurl-impersonate (a curl fork) and
BoringSSL. Their licence texts ride inside the wheel
and MUST be carried into THIRD_PARTY_LICENSES.md by
CC-1, read from the wheel itself, before ANY shipped
use of this lane. Until that CC-1 read lands, the lane
is a classified, gated helper — not an enabled shipped
route — and no shipped code calls it.
"""

from __future__ import annotations

from typing import Any, Callable

#: (status_code, body_text) for a transport. Tests inject
#: a fake; production passes None and gets the lazy
#: curl_cffi transport.
Transport = Callable[[str, list[dict[str, Any]], str, float],
                     tuple[int, str]]

BUNDLED_NOTICES = (
    "curl_cffi (MIT, primary-verified 2026-10-25) bundles "
    "native libraries inside its wheel: libcurl-impersonate "
    "(a curl fork) and BoringSSL. Their licence texts ride "
    "inside the wheel and must be carried into "
    "THIRD_PARTY_LICENSES.md by CC-1, read from the wheel "
    "itself, before any shipped use of this lane. This "
    "module does NOT paraphrase those texts, and no "
    "shipped path imports this module yet."
)


def _default_transport(url: str, cookies: list[dict[str, Any]],
                       impersonate: str, timeout: float,
                       ) -> tuple[int, str]:
    """The real transport, lazy-imported. Absent package =>
    a plain-words RuntimeError, never an ImportError
    leaking into a tool result, and never a fallback to
    another HTTP library (a silent fallback would be a
    different fingerprint than the one classified)."""
    try:
        from curl_cffi import requests as _curl_requests
    except Exception as exc:  # noqa: BLE001 — absence is
        raise RuntimeError(  # a supported state, not a bug
            "The curl_cffi read lane is unavailable: the "
            "optional curl_cffi package is not installed. "
            "This lane is never a shipped default dependency.") \
            from exc
    session = _curl_requests.Session(impersonate=impersonate)
    try:
        for cookie in cookies:
            session.cookies.set(cookie["name"], cookie["value"],
                                domain=cookie.get("domain") or "")
        response = session.get(url, timeout=timeout)
        return response.status_code, response.text
    finally:
        session.close()


def fetch_read(url: str, cookies: list[dict[str, Any]],
               *, cookie_source: str,
               impersonate: str = "chrome",
               timeout: float = 30.0,
               _transport: Transport | None = None,
               ) -> dict[str, Any]:
    """One classified, gated read-lane GET.

    Refusals (all plain-words, all before any network
    I/O, in this order): bad URL (HTTPS only), wrong
    cookie provenance, malformed cookies, disallowed
    impersonation target, tainted session (the lane's
    own egress refusal — see module docstring), absent
    curl_cffi package. On success: {"status": "success",
    "status_code": int, "text": str}. Cookie values
    never appear in any returned string.
    """
    from . import injection as _injection
    from .funnel.tools_gate import (
        CURL_CFFI_IMPERSONATE_ALLOWLIST,
        CURL_CFFI_COOKIE_SOURCE,
        curl_cffi_lane_class,
        curl_cffi_lane_needs_confirmation,
        playwright_cookies_ok,
    )  # Phase 3 F1: tools_gate moved to core/funnel/

    if curl_cffi_lane_class() != "egress":
        return {"status": "error", "kind": "refused",
                "message": "The curl_cffi read lane is not "
                           "classified as egress — refused."}
    if not isinstance(url, str) or not url.startswith("https://"):
        return {"status": "error", "kind": "validation",
                "message": "The read lane fetches HTTPS URLs only."}
    if cookie_source != CURL_CFFI_COOKIE_SOURCE:
        return {"status": "error", "kind": "refused",
                "message": ("Cookies for the read lane must come "
                            "from her own Playwright context "
                            "(cookie_source='playwright_context'). "
                            "No other cookie source is accepted, "
                            "and the lane takes no cookie path.")}
    cookie_error = playwright_cookies_ok(cookies)
    if cookie_error is not None:
        return {"status": "error", "kind": "validation",
                "message": cookie_error}
    if impersonate not in CURL_CFFI_IMPERSONATE_ALLOWLIST:
        return {"status": "error", "kind": "validation",
                "message": (f"Impersonation target '{impersonate}' "
                            "is not allowed for the read lane.")}
    try:
        tainted = bool(_injection.tainted())
    except Exception:  # noqa: BLE001 — A1 fail-closed: unconsultable
        # reads TAINTED, so the lane refuses (it has no popup to
        # ask through — a tainted lane is refused outright).
        # Reading clean on failure would open the egress read.
        tainted = True
    if curl_cffi_lane_needs_confirmation(tainted):
        return {"status": "error", "kind": "refused",
                "message": ("The read lane is refused while the "
                            "session is tainted: an egress read "
                            "under taint is a leak channel, and "
                            "this lane has no confirmation popup "
                            "of its own to ask through.")}
    transport = _transport or _default_transport
    try:
        status_code, text = transport(url, cookies, impersonate,
                                      float(timeout))
    except RuntimeError as exc:
        return {"status": "error", "kind": "unavailable",
                "message": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a transport
        return {"status": "error", "kind": "handler_error",  # failure
                "message": (f"The read lane request failed "
                            f"({type(exc).__name__}).")}
    return {"status": "success", "status_code": status_code,
            "text": text}
