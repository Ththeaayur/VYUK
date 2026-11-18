from __future__ import annotations

import os
import urllib.parse
import urllib.request

# INTENTIONAL (skills split B47): system-proxy family moved from
# live_client.py. Imports _log_line from the B46 error-cost module
# (the B46 out-edge: _apply_system_proxy_env resolves it).
from .live_client_error_cost import _log_line


_PROXY_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                   "http_proxy", "ALL_PROXY", "all_proxy")


def _apply_system_proxy_env() -> str | None:
    """Exports the detected system proxy into os.environ — guarded,
    best-effort, and silent on any surprise: an explicit proxy env
    var always wins (never overridden), an unusable value (a PAC
    URL, garbage) is ignored via the downloader's own validation
    (core.voice.local_assets._usable_proxy — the same rules downloads
    trust), and a direct connection that would have worked must
    never be broken by proxy discovery failing. The activity log
    records host:port only — proxy credentials never reach a log.

    Returns the exported proxy value when THIS call exported one
    (v5.29), else None — the connect path needs to know whether the
    proxy in the environment is our guess (removable for the
    direct retry) or the user's own setting (never touched)."""
    try:
        if any((os.environ.get(var) or "").strip()
               for var in _PROXY_ENV_VARS):
            return None  # the environment already speaks for itself
        try:
            found = urllib.request.getproxies() or {}
        except Exception:  # noqa: BLE001 — discovery must never break connect
            return None
        from .local_assets import _usable_proxy  # Phase 3 V2: local_assets now in core/voice/; V1b interim closed

        usable: dict[str, str] = {}
        for scheme in ("https", "http"):
            checked = _usable_proxy(found.get(scheme))
            if checked:
                usable[scheme] = checked
        if not usable:
            return None
        value = usable.get("https") or usable.get("http")
        os.environ["HTTPS_PROXY"] = value
        os.environ["HTTP_PROXY"] = value
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname or ""
        desc = f"{host}:{parts.port}" if parts.port else host
        _log_line(f"System proxy detected ({desc}) — exported to the "
                  "Google library's environment for the Live connect.")
        return value
    except Exception:  # noqa: BLE001 — proxy help is best-effort only
        return None


def _remove_exported_proxy(value: str) -> None:
    """v5.29: undo _apply_system_proxy_env's export — removes the
    two variables only while they still hold the value we set (a
    value the user or another component put there is left alone)."""
    try:
        for var in ("HTTPS_PROXY", "HTTP_PROXY"):
            if os.environ.get(var) == value:
                del os.environ[var]
    except Exception:  # noqa: BLE001 — best-effort, like the export
        pass
