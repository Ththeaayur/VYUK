"""Feed transport rules — split from updater.py (B24)."""
from __future__ import annotations

import urllib.request
from .updater_paths import _LOOPBACK_HOSTS  # INTENTIONAL: shared constant


def _transport_error(url: str) -> str | None:
    """None when `url` may be fetched, else the refusal reason.
    HTTPS everywhere; plaintext HTTP only to loopback hosts."""
    try:
        parsed = urllib.request.urlparse(url)
    except Exception:  # noqa: BLE001
        return f"unparseable URL: {url!r}"
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    if scheme == "https":
        return None
    if scheme == "http" and host in _LOOPBACK_HOSTS:
        return None
    if scheme == "http":
        return ("plaintext HTTP update URLs are refused — the feed "
                "and the update zip must be served over HTTPS")
    return f"unsupported update URL scheme: {scheme or '(none)'}"


def _opener_for(url: str) -> urllib.request.OpenerDirector:
    """System-proxy opener (v3.3 behaviour), except loopback hosts,
    which are never proxied."""
    host = (urllib.request.urlparse(url).hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({}))
    proxies = urllib.request.getproxies()
    if proxies:
        return urllib.request.build_opener(
            urllib.request.ProxyHandler(proxies))
    return urllib.request.build_opener()


def _configured_feed_url() -> str:
    try:
        from ..config import config  # Phase 3 H1: target stays flat in core/
        return (getattr(config, "update_feed_url", "") or "").strip()
    except Exception:  # noqa: BLE001 — a config hiccup means "no feed"
        return ""
