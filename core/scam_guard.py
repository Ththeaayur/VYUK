"""Money track (v5.66) — Leaf 3: scam checks + guided reporting.

MN-6.9 (Sanchar Saathi / Chakshu guided reporting, STAR), MN-6 scam
checks in LOCAL-HASH mode only, and MN-6.7 VPA read-back.

LOCAL-ONLY BY CONSTRUCTION. This module performs NO network I/O and
contains NO send path:

* It never imports http / socket / urllib / requests / webbrowser.
* There is no send / submit / upload / post function in this module.
  ``build_report_prefill`` prepares a draft that stays on the machine;
  the USER sends it, in their own browser or by their own phone call.
  VYUK sends NOTHING to DoT, to Sanchar Saathi, or to the cybercrime
  portal.
* ``deep_link_for`` returns a portal URL string ONLY. It never opens
  it: the coordinator/GUI may open it only on an explicit user tap.
* Scam-URL checking compares a locally computed SHA-256 hash against
  a caller-supplied LOCAL set of hashes. The full-URL Lookup mode
  (MN-6.2 as written) is REFUSED: sending the full URL to a remote
  lookup service would disclose the user's browsing and breaks
  INV-4/19, so ``check_url`` accepts mode ``"local-hash"`` only and
  ``lookup_url_full`` always raises ``FullUrlLookupRefused``.
* VPA validation is READ-BACK ONLY: ``read_back_payee`` returns the
  bank-registered name (via a caller-injected resolver) BEFORE any
  money moves, and this module itself never moves money — there is
  no pay / transfer / move function here. ``gate_before_move`` is
  the gate the coordinator must check before any payment path.
* The one-merchant-key-serving-many-users shape (MN-6.7/6.8) is
  STILL-OWED and NOT built: ``request_merchant_key_multi_user``
  raises ``MerchantKeyShapeNotBuilt``.

1930 vs cybercrime distinction — kept EXACT (source: the v5.66 money
track brief, plan section 5.11):

* 1930 is the financial fraud helpline.
* The cybercrime portal is for other cybercrime reporting.

No other helpline number is stated anywhere in this module; do not
add one without a verified primary source.

Stdlib only (hashlib + re). No new dependency, no requirements.txt
change, no tool declarations added by this leaf.
"""

from __future__ import annotations

import hashlib
import re

# ---------------------------------------------------------------------------
# Pre-pinned constants (label + assertion + comment together, per the
# money-track brief: every constant/string this diff moves is pinned
# verbatim by test_v566_leaf3_scam.py — change a string here and the
# suite must change with it, never silently).
# ---------------------------------------------------------------------------

# 1930 is the financial fraud helpline. (Pinned verbatim.)
HELPLINE_1930 = "1930"
COPY_1930 = "1930 is the financial fraud helpline."
# The cybercrime portal is for other cybercrime reporting. (Pinned.)
COPY_CYBERCRIME_PORTAL = "The cybercrime portal is for other cybercrime reporting."
# Chakshu copy: suspected fraud communications only, nothing wider.
COPY_CHAKSHU = (
    "Chakshu on Sanchar Saathi is for reporting suspected fraud communications."
)
COPY_SANCHAR_SAATHI = (
    "Sanchar Saathi is for reporting suspected fraud communications."
)

CYBERCRIME_PORTAL_URL = "https://cybercrime.gov.in"
SANCHAR_SAATHI_URL = "https://sancharsaathi.gov.in"

# Full-URL Lookup refusal reason (pinned: names the invariants it breaks).
FULL_URL_REFUSAL = (
    "Refused: full-URL Lookup mode would send the full URL to a remote "
    "service (INV-4/19). Use local-hash comparison instead."
)

# One-merchant-key-serving-many-users closing status (pinned).
MERCHANT_KEY_MULTI_USER_STATUS = (
    "STILL-OWED — not built: written aggregator confirmation owed (MN-6.7/6.8)."
)


class FullUrlLookupRefused(Exception):
    """Raised for any full-URL Lookup attempt (MN-6.2 as written)."""


class MerchantKeyShapeNotBuilt(Exception):
    """Raised for the STILL-OWED one-merchant-key shape (MN-6.7/6.8)."""


# ---------------------------------------------------------------------------
# MN-6.9 — reporting routes + LOCAL pre-fill (she sends NOTHING to DoT)
# ---------------------------------------------------------------------------

# Route table. "channel" always names an action by the USER, never by
# VYUK: VYUK prepares, the user sends. The 1930 route has no URL —
# it is a phone helpline, not a website.
REPORTING_ROUTES = (
    {
        "id": "call_1930",
        "name": "1930",
        "copy": COPY_1930,
        "channel": "Phone call made by you, not by VYUK.",
        "url": None,
    },
    {
        "id": "chakshu",
        "name": "Sanchar Saathi — Chakshu",
        "copy": COPY_CHAKSHU,
        "channel": "Website opened by you, not by VYUK.",
        "url": SANCHAR_SAATHI_URL,
    },
    {
        "id": "sanchar_saathi",
        "name": "Sanchar Saathi",
        "copy": COPY_SANCHAR_SAATHI,
        "channel": "Website opened by you, not by VYUK.",
        "url": SANCHAR_SAATHI_URL,
    },
    {
        "id": "cybercrime_portal",
        "name": "National Cybercrime Reporting Portal",
        "copy": COPY_CYBERCRIME_PORTAL,
        "channel": "Website opened by you, not by VYUK.",
        "url": CYBERCRIME_PORTAL_URL,
    },
)

_ROUTE_BY_ID = {route["id"]: route for route in REPORTING_ROUTES}

# Category -> suggested route. Financial fraud goes to 1930 FIRST
# (the helpline), fraud communications go to Chakshu, everything
# else cyber goes to the cybercrime portal.
_CATEGORY_ROUTE = {
    "financial_fraud": "call_1930",
    "fraud_call": "chakshu",
    "fraud_sms": "chakshu",
    "fraud_message": "chakshu",
    "other_cybercrime": "cybercrime_portal",
}


def reporting_routes() -> list:
    """Return the reporting routes as plain dicts (copies, no send).

    Callers display these; nothing here contacts any portal.
    """
    return [dict(route) for route in REPORTING_ROUTES]


def deep_link_for(route_id: str) -> str | None:
    """Return the portal URL string for a route, or None for 1930.

    Returns a STRING ONLY — it never opens a browser and never
    fetches anything. The 1930 route has no URL: it is a phone
    helpline the user calls themselves.
    """
    route = _ROUTE_BY_ID.get(route_id)
    if route is None:
        raise ValueError("unknown reporting route: %r" % (route_id,))
    return route["url"]


def build_report_prefill(
    *,
    category: str,
    suspect_contact: str = "",
    channel: str = "",
    description: str = "",
    occurred_at: str = "",
    amount_lost: str = "",
) -> dict:
    """Build a LOCAL scam-report draft. Nothing is sent anywhere.

    ``category`` must be one of the known categories (fail closed on
    an unknown category rather than mis-routing a report). The
    returned dict always carries ``sent: False``,
    ``sent_to_dot: False``, ``egress: False`` and
    ``local_only: True`` — there is no code path in this module that
    flips them, because there is no send function to call.
    """
    if category not in _CATEGORY_ROUTE:
        raise ValueError(
            "unknown report category: %r (expected one of %s)"
            % (category, ", ".join(sorted(_CATEGORY_ROUTE)))
        )
    route_id = _CATEGORY_ROUTE[category]
    return {
        "category": category,
        "suggested_route": route_id,
        "route_copy": _ROUTE_BY_ID[route_id]["copy"],
        "suspect_contact": suspect_contact,
        "channel": channel,
        "description": description,
        "occurred_at": occurred_at,
        "amount_lost": amount_lost,
        "sent": False,
        "sent_to_dot": False,
        "egress": False,
        "local_only": True,
    }


def prefill_text(prefill: dict) -> str:
    """Render a pre-fill dict as plain copy-paste text for the user.

    The footer states plainly that nothing has been sent: the user
    pastes this into the portal themselves, or reads the 1930 facts
    over their own phone call.
    """
    lines = [
        "Scam report draft (prepared locally — NOT sent anywhere):",
        "Category: %s" % prefill.get("category", ""),
        "Suggested route: %s" % prefill.get("suggested_route", ""),
        "Suspect contact: %s" % prefill.get("suspect_contact", ""),
        "Channel: %s" % prefill.get("channel", ""),
        "What happened: %s" % prefill.get("description", ""),
        "When: %s" % prefill.get("occurred_at", ""),
        "Amount lost: %s" % prefill.get("amount_lost", ""),
        "VYUK has sent nothing to DoT, Sanchar Saathi, or the "
        "cybercrime portal. You send this yourself.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MN-6 — scam-URL checks, LOCAL-HASH mode only
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"^https?://([^/]+)(.*)$", re.IGNORECASE)


def normalise_url(url: str) -> str:
    """Normalise a URL for LOCAL hashing: strip whitespace, lowercase
    scheme + host, keep path/query as-is. Raises ValueError on empty
    or non-http(s) input (fail closed, never hash garbage as clean).
    """
    if not isinstance(url, str):
        raise ValueError("URL must be text")
    text = url.strip()
    match = _URL_RE.match(text)
    if match is None:
        raise ValueError("not an http(s) URL: %r" % (url,))
    scheme_host = text[: match.start(2)].lower()
    return scheme_host + match.group(2)


def hash_url(url: str) -> str:
    """SHA-256 hex digest of the normalised URL, computed LOCALLY."""
    return hashlib.sha256(normalise_url(url).encode("utf-8")).hexdigest()


def check_url_local_hash(url: str, known_bad_hashes) -> dict:
    """Compare a URL's LOCAL hash against a LOCAL set of bad hashes.

    ``known_bad_hashes`` is supplied by the caller (e.g. loaded with
    ``load_local_hash_set`` from a file on this machine). No hash,
    URL, or anything else leaves the machine in this function.
    Verdicts: "match" (hash in the local bad set), "no_match",
    or "invalid" (unparseable URL — never reported as clean).
    """
    try:
        digest = hash_url(url)
        normalised = normalise_url(url)
    except ValueError as exc:
        return {
            "url": url,
            "normalised": None,
            "sha256": None,
            "verdict": "invalid",
            "reason": str(exc),
            "mode": "local-hash",
            "egress": False,
        }
    bad = set(known_bad_hashes or ())
    return {
        "url": url,
        "normalised": normalised,
        "sha256": digest,
        "verdict": "match" if digest in bad else "no_match",
        "reason": (
            "Local hash matches the local bad-hash set."
            if digest in bad
            else "Local hash not found in the local bad-hash set."
        ),
        "mode": "local-hash",
        "egress": False,
    }


def check_url(url: str, known_bad_hashes=None, mode: str = "local-hash") -> dict:
    """URL scam check. ONLY mode ``"local-hash"`` is accepted.

    Any other mode — including ``"full-url-lookup"`` / ``"lookup"`` —
    raises ``FullUrlLookupRefused``: MN-6.2's full-URL Lookup mode is
    refused as written (INV-4/19).
    """
    if mode != "local-hash":
        raise FullUrlLookupRefused(FULL_URL_REFUSAL)
    return check_url_local_hash(url, known_bad_hashes)


def lookup_url_full(url: str):  # noqa: ARG001 — refused, never used
    """MN-6.2 full-URL Lookup mode: ALWAYS REFUSED. Never implemented.

    Kept as an explicit refusal point so a caller asking for the
    remote-lookup shape gets a loud refusal instead of a silent
    fallback to something else.
    """
    raise FullUrlLookupRefused(FULL_URL_REFUSAL)


def load_local_hash_set(path: str) -> set:
    """Load a LOCAL bad-hash set from a text file on this machine.

    One SHA-256 hex digest per line; blank lines and ``#`` comments
    ignored. Local file I/O only — no network.
    """
    hashes = set()
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            entry = line.strip().lower()
            if entry and not entry.startswith("#"):
                hashes.add(entry)
    return hashes


# ---------------------------------------------------------------------------
# MN-6.7 — VPA validation, READ-BACK ONLY, before money moves
# ---------------------------------------------------------------------------

_VPA_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]{2,}$")


def is_valid_vpa_syntax(vpa: str) -> bool:
    """Syntax-only VPA shape check (``localpart@bankhandle``).

    Syntax is NOT bank verification: a well-formed VPA still needs
    its registered name read back before money moves.
    """
    return isinstance(vpa, str) and _VPA_RE.match(vpa) is not None


def read_back_payee(vpa: str, resolve_name) -> dict:
    """Read the bank-registered name back for a VPA, BEFORE any move.

    ``resolve_name`` is a caller-injected callable (the bank/PSP
    lookup lives with the caller, never in this module): it takes
    the VPA verbatim and returns the bank-registered name, or None
    when no name resolves. The VPA is copied character-for-character
    — never normalised, never guessed.

    This function moves NO money: the result always carries
    ``money_moved: False`` and there is no pay function in this
    module to call afterwards. The coordinator must pass this
    result through ``gate_before_move`` before any payment path.
    """
    if not is_valid_vpa_syntax(vpa):
        raise ValueError("not a valid VPA shape: %r" % (vpa,))
    name = resolve_name(vpa)
    name = name.strip() if isinstance(name, str) else None
    if not name:
        name = None
    return {
        "vpa": vpa,
        "registered_name": name,
        "name_read_back": name is not None,
        "money_moved": False,
        "moved": False,
    }


def gate_before_move(readback: dict) -> tuple:
    """Gate: may money move for this read-back? (allowed, reason).

    Allowed ONLY when a bank-registered name was actually read back.
    Anything else — unresolved name, missing read-back — FAILS
    CLOSED: the coordinator must not proceed to any payment path.
    """
    if not isinstance(readback, dict):
        return (False, "No VPA read-back present — money must not move.")
    if readback.get("money_moved"):
        return (False, "Read-back already claims money moved — refusing.")
    if readback.get("name_read_back") and readback.get("registered_name"):
        return (
            True,
            "Bank-registered name read back: %s. Gate passed; "
            "the payment itself is the caller's separate step."
            % readback["registered_name"],
        )
    return (
        False,
        "No bank-registered name read back for this VPA — "
        "money must not move until the name is read back.",
    )


def readback_summary(readback: dict) -> str:
    """Plain-language summary of a VPA read-back (no money moved)."""
    if readback.get("name_read_back") and readback.get("registered_name"):
        return (
            "Bank-registered name read back for %s: %s. "
            "No money has moved."
            % (readback.get("vpa", ""), readback["registered_name"])
        )
    return (
        "No bank-registered name could be read back for %s. "
        "No money has moved." % readback.get("vpa", "")
    )


def request_merchant_key_multi_user():  # noqa: ARG001
    """One-merchant-key-serving-many-users: STILL-OWED, NOT BUILT.

    Raises rather than returning a key shape: no merchant key is
    issued, shared, or served from this module.
    """
    raise MerchantKeyShapeNotBuilt(MERCHANT_KEY_MULTI_USER_STATUS)
