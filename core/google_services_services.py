"""Part of the core/google_services.py split (B25): google_services_services. Move only — see core/google_services.py for the facade."""

import json
from .google_services_helpers import _LIBS_MISSING, _short  # INTENTIONAL (B25): shared names (_failure_message returns _LIBS_MISSING)
from .google_services_token import _load_credentials, _load_credentials_detailed, _read_token_text, is_connected  # INTENTIONAL (B25): token family (_resolve_service calls is_connected + _load_credentials_detailed)


NOT_CONNECTED = (
    "Google isn't connected yet — open Settings → Your world and press "
    "Connect Google."
)


_AUTH_EXPIRED = (
    "Your Google sign-in has expired or was revoked — open Settings "
    "→ Your world and press Connect Google to sign in again."
)


def _resolve_service(api: str, version: str):
    """(service, state, detail) — the one place the world tools'
    failure taxonomy is decided (v4.6, audit F12). The old helpers
    collapsed every failure into None, and every handler translated
    None into the single "isn't connected yet" string — false for a
    user whose token was revoked (Settings showed "Connected") and
    useless when the libraries were simply missing. States:
    "not_connected" / "libs_missing" / "auth" / "api" (signed in,
    but the API service itself couldn't be built) / "ok"."""
    if not is_connected():
        return None, "not_connected", ""
    creds, state, detail = _load_credentials_detailed()
    if creds is None:
        return None, state, detail
    try:
        from googleapiclient.discovery import build
    except ImportError as e:  # noqa: BLE001
        return None, "libs_missing", _short(e)
    try:
        service = build(api, version, credentials=creds,
                        cache_discovery=False)
    except ImportError as e:  # noqa: BLE001
        return None, "libs_missing", _short(e)
    except Exception as e:  # noqa: BLE001
        return None, "api", _short(e)
    return service, "ok", ""


def _get_service(api: str, version: str):
    """Builds an API service from the stored credentials, or None when
    not connected / the libraries are missing."""
    service, _state, _detail = _resolve_service(api, version)
    return service


_last_service_state: dict[str, tuple[str, str]] = {}


def _calendar_service_or_none():
    service, state, detail = _resolve_service("calendar", "v3")
    _last_service_state["calendar"] = (state, detail)
    return service


def _gmail_service_or_none():
    service, state, detail = _resolve_service("gmail", "v1")
    _last_service_state["gmail"] = (state, detail)
    return service


def _failure_message(state: str, detail: str, thing: str) -> str:
    """The accurate sentence for a no-service outcome — each state
    names the actual problem and the step that fixes it."""
    if state == "libs_missing":
        return _LIBS_MISSING
    if state == "auth":
        return _AUTH_EXPIRED
    if state == "api":
        return (
            f"Google is connected, but VYUK couldn't reach {thing} "
            f"just now ({detail}). Try again in a moment."
        )
    return NOT_CONNECTED


def _service_or_failure(kind: str, thing: str):
    """(service, failure_message) for the speakable tools — exactly
    one of the pair is meaningful. Goes through the *_or_none seam
    and phrases the state it recorded when there is no service."""
    if kind == "calendar":
        service = _calendar_service_or_none()
    else:
        service = _gmail_service_or_none()
    if service is not None:
        return service, ""
    state, detail = _last_service_state.get(kind, ("not_connected", ""))
    return None, _failure_message(state, detail, thing)


def _scope_missing_message(action: str) -> str:
    """The scope state's sentence: the sign-in is real and the reads
    work, but it predates the write permission. The fix it names —
    one reconnect — is exactly what grants the missing scope,
    because connect() always consents with the current SCOPES."""
    return (
        f"Google is connected for reading, but {action} needs one "
        "more permission — reconnect Google in Settings to grant it."
    )


def _granted_scopes(creds):
    """The scopes the stored sign-in was ACTUALLY granted, as a set —
    or None when that can't be determined (older token files, odd
    credential objects). None means "don't guess": the write proceeds
    and the API's own refusal is classified instead
    (_is_scope_error).

    Sources, in order: the credentials' ``granted_scopes``
    (google-auth records what the server granted, which is distinct
    from what was asked for), then the "scopes" list saved in the
    token file itself at consent time. The credentials' plain
    ``.scopes`` is deliberately NOT used: it echoes the requested
    SCOPES the token is loaded with — write scopes included —
    whether or not they were ever granted."""
    granted = getattr(creds, "granted_scopes", None)
    if granted:
        try:
            return {str(s) for s in granted}
        except Exception:  # noqa: BLE001
            pass
    try:
        text = _read_token_text()
        data = json.loads(text) if text is not None else None
        saved = data.get("scopes") if isinstance(data, dict) else None
        if isinstance(saved, list) and saved:
            return {str(s) for s in saved}
    except Exception:  # noqa: BLE001
        pass
    return None


def _resolve_write_service(api: str, version: str, required_scope: str):
    """(service, state, detail) for the WRITE tools: _resolve_service
    plus the "scope" state. A token granted before the write scopes
    existed is fully valid for reading (is_connected() is True and
    every read works) yet Google will refuse a send made with it —
    that is its own state, never "not_connected"."""
    service, state, detail = _resolve_service(api, version)
    if service is None:
        return None, state, detail
    creds = _load_credentials()
    granted = _granted_scopes(creds) if creds is not None else None
    if granted is not None and required_scope not in granted:
        return None, "scope", required_scope
    return service, "ok", ""


def _write_service_or_failure(kind: str, thing: str,
                              required_scope: str, action: str):
    """(service, failure_message) for the write tools — the read
    taxonomy's four states phrase exactly as before (via
    _failure_message); the scope state gets its own sentence."""
    api, version = ("gmail", "v1") if kind == "gmail" else ("calendar", "v3")
    service, state, detail = _resolve_write_service(api, version,
                                                    required_scope)
    if service is not None:
        return service, ""
    if state == "scope":
        return None, _scope_missing_message(action)
    return None, _failure_message(state, detail, thing)


_SCOPE_ERROR_MARKERS = (
    "insufficientpermissions",            # the API's reason field
    "insufficient permission",            # its human-readable message
    "insufficient authentication scopes",
    "access_token_scope_insufficient",
)


def _is_scope_error(exc: Exception) -> bool:
    """True when an API refusal is really a missing-scope refusal —
    the fallback classifier for sign-ins whose granted scopes can't
    be inspected up front (_granted_scopes returned None)."""
    text = str(exc).lower()
    return any(marker in text for marker in _SCOPE_ERROR_MARKERS)
