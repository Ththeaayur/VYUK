"""Browser denylist store (click-anywhere Batch 1, Plan 9 §1.2, M-8/N-16/N-17).

Own code — nothing adopted (Parent-ruled (licence, 2026-11-14)).

A settings list in app data, versioned JSON, maintained by the owner.
Default EMPTY — it is never shipped with named third-party domains he
did not name. Matched by exact domain + subdomains. The capture_guard
title-keyword lists still apply separately; they are NOT duplicated
here.

File: ``<app data>/browser_denylist.json``::

    {"version": 1, "domains": ["example-bank.com"]}

The path is resolved by the tree's app-data pattern
(``os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")``)
— never from the HKCU key.

Next step (follow-up, NOT built here): the Settings "Browser denylist"
UI (add/remove domains) + the voice command ("add <domain> to the
browser denylist"). Both call :func:`add_domain` / :func:`remove_domain`
in this module.
"""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

_VERSION = 1
_FILENAME = "browser_denylist.json"


def _app_data_dir() -> Path:
    return Path(os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming"))


def denylist_path() -> Path:
    """Full path of the denylist file (app data)."""
    return _app_data_dir() / _FILENAME


def _normalise_domain(value: str) -> str:
    """Lower-case a domain, stripping scheme, port, path and a trailing dot.

    Accepts a bare domain (``bank.example.com``), a URL
    (``https://www.bank.example.com/login``) or a bare host with a port.
    Returns ``""`` when nothing usable remains.
    """
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).netloc or raw.split("://", 1)[1]
    raw = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in raw:  # userinfo@host — keep only the host
        raw = raw.rsplit("@", 1)[1]
    if raw.startswith("["):  # IPv6 literal — not a denylist domain
        return ""
    if ":" in raw:  # host:port — keep only the host
        raw = raw.split(":", 1)[0]
    return raw.rstrip(".")


def _host_of(value: str) -> str:
    """Host part of a URL or origin, lower-cased, or ``""``."""
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).hostname or ""
    except ValueError:
        return ""
    return host.rstrip(".")


def _read_file() -> dict:
    path = denylist_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": _VERSION, "domains": []}
    except (OSError, ValueError):
        # Corrupt file: fail closed to an empty list rather than
        # guessing at content. The file is rewritten on the next add.
        return {"version": _VERSION, "domains": []}
    if isinstance(data, list):  # tolerate a bare-list legacy shape
        return {"version": _VERSION, "domains": data}
    if not isinstance(data, dict):
        return {"version": _VERSION, "domains": []}
    domains = data.get("domains", [])
    if not isinstance(domains, list):
        domains = []
    clean = []
    for item in domains:
        norm = _normalise_domain(str(item))
        if norm and norm not in clean:
            clean.append(norm)
    return {"version": _VERSION, "domains": sorted(clean)}


def _write_file(domains: list) -> None:
    path = denylist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": _VERSION, "domains": sorted(domains)}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_denylist() -> list:
    """The denylisted domains, sorted; ``[]`` when nothing is stored yet."""
    return _read_file()["domains"]


def list_domains() -> list:
    """Alias of :func:`load_denylist` (the Settings UI reads this)."""
    return load_denylist()


def add_domain(domain: str) -> list:
    """Add a domain (exact + subdomains). Returns the updated list.

    Raises :class:`ValueError` on an empty/unusable domain.
    """
    norm = _normalise_domain(domain)
    if not norm:
        raise ValueError(f"Not a usable domain: {domain!r}")
    domains = load_denylist()
    if norm not in domains:
        domains.append(norm)
        _write_file(domains)
    return sorted(domains)


def remove_domain(domain: str) -> list:
    """Remove a domain (no-op when absent). Returns the updated list."""
    norm = _normalise_domain(domain)
    domains = [d for d in load_denylist() if d != norm]
    _write_file(domains)
    return domains


def _domain_matches(host: str, domain: str) -> bool:
    """Exact domain + subdomains: ``host == domain`` or ``host`` ends
    with ``"." + domain``. A suffix that is not a subdomain boundary
    (``notbank.com`` vs ``bank.com``) does NOT match."""
    host = (host or "").lower().rstrip(".")
    domain = (domain or "").lower().rstrip(".")
    return bool(host and domain) and (host == domain or host.endswith("." + domain))


def is_denied_url(url: str) -> bool:
    """True when the URL's host is denylisted (exact domain or subdomain)."""
    host = _host_of(url)
    if not host:
        return False
    return any(_domain_matches(host, d) for d in load_denylist())


def origin_denied(origin: str) -> bool:
    """Per-frame origin check (N-17): True when the frame's origin host
    is denylisted. A denied-origin frame's content is refused by name,
    never silently skipped — the caller reports the refusal."""
    return is_denied_url(origin)
