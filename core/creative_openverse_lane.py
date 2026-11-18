"""CR-6.4 Openverse lane, pilot (Creative track, wave 29).

Search lane with the pilot gates AS CODE (stdlib ``urllib`` only):
- the commercial filter is HARD-SET to ``license_type=commercial``.
  No function here takes a licence parameter callers could relax.
- blocklist: NC/ND leakage is dropped; CC BY-SA is dropped for
  commercial composites (share-alike surprises in commercial work).
- every kept hit returns a human-visible licence card AND a ledger
  entry linking the source page for re-verification.
- NEVER auto-trust the licence field: Openverse itself disclaims
  accuracy, so every card carries the source page for the human to
  re-verify. The ledger entry records the claim as a claim.

OFF-DEVICE RULE (binding): the API call sends query terms
off-device, so every search function takes an explicit per-search
opt-in flag and REFUSES without it (telemetry-OFF product). The
refusal happens BEFORE any network call — a refused search sends
nothing. Local cache is opt-in storage in a caller-provided
directory; with no cache dir, nothing is cached.

Pexels lane: QUEUED as the second lane (its API bans ML-training /
dataset use + standalone sale + bulk copying — those rules must be
encoded before any lane ships). Unsplash/Pixabay: NO code (direct
primary reads owed). Splice lane: QUEUED (full Terms text owed).
Looperman: NO fetcher — the ledger rule lives in creative_rights_ledger
(no per-loop text -> non-commercial default).

Stdlib only (urllib, json, hashlib, os).
"""

import hashlib
import json
import os
import urllib.parse
import urllib.request

API_BASE = "https://api.openverse.org/v1/images/"
_LICENSE_TYPE_HARD_SET = "commercial"


def build_search_url(query, page_size=20):
    """Build the search URL. The commercial filter is hard-set here
    and nowhere else — callers cannot pass a licence parameter."""
    params = {
        "q": query,
        "license_type": _LICENSE_TYPE_HARD_SET,
        "page_size": page_size,
    }
    return API_BASE + "?" + urllib.parse.urlencode(params)


def _licence_parts(licence):
    return (licence or "").strip().lower().replace("_", "-").split("-")


def _classify_hit(licence, purpose):
    """Return (keep, reason). NC/ND always dropped; BY-SA dropped for
    commercial composites; anything else kept with its card."""
    parts = _licence_parts(licence)
    if "nc" in parts:
        return False, "non-commercial licence (NC) dropped"
    if "nd" in parts:
        return False, "no-derivatives licence (ND) dropped"
    if licence and licence.strip().lower() in ("by-sa", "cc-by-sa") \
            and purpose == "commercial":
        return False, "share-alike licence dropped for a commercial" \
                      " composite (re-verify before any composite use)"
    return True, ""


def _licence_card(item):
    return {
        "title": item.get("title", ""),
        "licence": item.get("license", ""),
        "licence_url": item.get("license_url", ""),
        "licence_version": item.get("license_version", ""),
        "creator": item.get("creator", ""),
        "source_page": item.get("foreign_landing_url", ""),
        "file_url": item.get("url", ""),
        "attribution_text": item.get("attribution", ""),
        "verify_note": "Openverse disclaims licence-field accuracy:"
                       " re-verify at the source page before use.",
    }


def _cache_path(cache_dir, query):
    key = hashlib.sha1(query.encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, "openverse_%s.json" % key)


def search(query, opt_in=False, purpose="commercial", fetcher=None,
           cache_dir=None, ledger_writer=None):
    """Search Openverse. ``opt_in`` is per-search and mandatory.

    ``fetcher`` is a callable taking a URL and returning parsed JSON
    (tests inject one; production uses the urllib default). When
    ``ledger_writer`` is given, it is called with the list of ledger
    entries (one per kept hit, each linking its source page) — the
    writer belongs to later wiring (creative_rights_ledger's writer), not to
    this module, so this module never imports the ledger.
    """
    if opt_in is not True:
        return {"ok": False, "refused": True,
                "reason": "per-search opt-in required: no request sent,"
                          " nothing left the device"}
    if cache_dir:
        path = _cache_path(cache_dir, query)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                cached = json.load(handle)
            cached["from_cache"] = True
            return cached
    url = build_search_url(query)
    if fetcher is not None:
        data = fetcher(url)
    else:
        req = urllib.request.Request(
            url, headers={"User-Agent": "VYUK/1.0 (Openverse pilot)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    kept = []
    dropped = []
    for item in data.get("results", []):
        keep, reason = _classify_hit(item.get("license", ""), purpose)
        if keep:
            kept.append(_licence_card(item))
        else:
            dropped.append({"title": item.get("title", ""),
                            "licence": item.get("license", ""),
                            "reason": reason})
    result = {"ok": True, "from_cache": False, "results": kept,
              "dropped": dropped, "query": query}
    if ledger_writer is not None:
        entries = [{
            "source": "Openverse",
            "licensor": card["creator"],
            "licence_name": card["licence"],
            "licence_url": card["licence_url"],
            "download_provenance":
                "Openverse hit; re-verify at source page: %s"
                % card["source_page"],
        } for card in kept]
        ledger_writer(entries)
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir, query), "w",
                  encoding="utf-8") as handle:
            json.dump(result, handle)
    return result
