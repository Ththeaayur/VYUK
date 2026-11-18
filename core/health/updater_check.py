"""check() — split from updater.py (B24)."""
from __future__ import annotations

import json
import urllib.request
from ..version import VERSION, parse_version, version_newer
from .updater_paths import _CHECK_TIMEOUT_S, _log  # INTENTIONAL: shared helper/constant
from .updater_policy import _policy_refusal  # INTENTIONAL: shared helper
from .updater_transport import _configured_feed_url, _opener_for, _transport_error  # INTENTIONAL: shared helpers
from .updater_verify import _verify_feed_signature  # INTENTIONAL: shared helper


def check(feed_url: str | None = None) -> dict:
    """Asks the feed whether a newer VYUK exists. Never raises.

    States: "no_feed" (no URL configured), "untrusted" (the
    manifest's detached Ed25519 signature is missing or does not
    verify against the pinned key — no field of the manifest is
    trusted, parsed or returned), "up_to_date", "available"
    (version/url/sha256/notes of the newer build), "error"
    (transport refusal, network failure, malformed feed —
    `message` says which).
    """
    url = (feed_url if feed_url is not None
           else _configured_feed_url()).strip()
    if not url:
        return {"state": "no_feed", "current_version": VERSION}
    # Gate C (P2-2): the policy gate sits BEFORE any network
    # use — a paused update never even reads the feed.
    policy_refusal = _policy_refusal("update")
    if policy_refusal is not None:
        return {"state": "error", "message": policy_refusal,
                "current_version": VERSION}
    refusal = _transport_error(url)
    if refusal is not None:
        return {"state": "error", "message": refusal,
                "current_version": VERSION}
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": f"VYUK-Updater/{VERSION}"})
        with _opener_for(url).open(request,
                                   timeout=_CHECK_TIMEOUT_S) as resp:
            raw = resp.read(256 * 1024)
    except Exception as exc:  # noqa: BLE001 — report, never raise
        return {"state": "error",
                "message": f"couldn't read the update feed: {exc}"[:300],
                "current_version": VERSION}
    # Authenticate BEFORE parsing: nothing in the manifest — not
    # even its version field — is looked at until the pinned key
    # has vouched for the exact bytes.
    untrusted = _verify_feed_signature(url, raw)
    if untrusted is not None:
        _log(f"update feed at {url} failed signature verification")
        return {"state": "untrusted", "message": untrusted,
                "current_version": VERSION}
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — report, never raise
        return {"state": "error",
                "message": f"couldn't read the update feed: {exc}"[:300],
                "current_version": VERSION}
    if not isinstance(manifest, dict):
        return {"state": "error",
                "message": "update feed is not a JSON object",
                "current_version": VERSION}
    remote = manifest.get("version")
    if parse_version(remote) is None:
        return {"state": "error",
                "message": f"update feed has no usable version "
                           f"({remote!r})",
                "current_version": VERSION}
    if not version_newer(remote, VERSION):
        return {"state": "up_to_date", "version": VERSION,
                "remote_version": remote}
    # A newer build exists: the manifest must be complete enough to
    # stage safely, or it counts as a broken feed, not an update.
    zip_url = manifest.get("url")
    sha = manifest.get("sha256")
    if not isinstance(zip_url, str) or not zip_url.strip():
        return {"state": "error",
                "message": "update feed names no download URL",
                "current_version": VERSION}
    if not isinstance(sha, str) or len(sha.strip()) != 64 or \
            any(c not in "0123456789abcdefABCDEF" for c in sha.strip()):
        return {"state": "error",
                "message": "update feed has no valid sha256 for the "
                           "new build — refusing to offer it",
                "current_version": VERSION}
    min_version = manifest.get("min_version")
    if min_version is not None and parse_version(min_version) is None:
        return {"state": "error",
                "message": f"update feed has an unusable min_version "
                           f"({min_version!r})",
                "current_version": VERSION}
    applies = True
    if min_version is not None and version_newer(min_version, VERSION):
        # The running build is BELOW the minimum this update can be
        # applied from (a stepping-stone release is required first).
        applies = False
    return {
        "state": "available",
        "version": remote,
        "url": zip_url.strip(),
        "sha256": sha.strip().lower(),
        "notes": str(manifest.get("notes") or ""),
        "min_version": min_version,
        "applies": applies,
        "current_version": VERSION,
    }
