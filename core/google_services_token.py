"""Part of the core/google_services.py split (B25): google_services_token. Move only — see core/google_services.py for the facade."""

import datetime
import json
import os
from pathlib import Path
from .google_services_helpers import _short  # INTENTIONAL (B25): shared shortener
from .google_services_helpers import CALENDAR_EVENTS_SCOPE, GMAIL_SEND_SCOPE, _LIBS_MISSING  # INTENTIONAL (B25): SCOPES is built from the scope constants; connect() returns _LIBS_MISSING


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    GMAIL_SEND_SCOPE,
    CALENDAR_EVENTS_SCOPE,
]


_CONNECTED_OK = (
    "Connected to Google — VYUK can now read your calendar and mail, "
    "and can send mail or add events only with your OK each time."
)


_DISCONNECTED_OK = "Disconnected — VYUK no longer has access to your Google account."


_NO_CODES = (
    "Add your Google client codes first — open Settings → Your world, "
    "paste the client ID and secret from your Google Cloud project, "
    "then press Connect Google again."
)


def _token_path() -> Path:
    """Where the OAuth token lives: the project data dir, next to the
    memory DB (see core/config.py DATA_DIR)."""
    from .config import DATA_DIR

    return DATA_DIR / "google_token.json"


def _save_token_text(text: str) -> None:
    """Writes the token file with its content protected (v5.47,
    programme item 14): what lands on disk is the secrets_store
    envelope of the token JSON, via the same tmp + os.replace
    atomic-write discipline as before."""
    from . import secrets_store

    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(secrets_store.protect_text(text), encoding="utf-8")
    os.replace(tmp, path)


def _read_token_text() -> str | None:
    """The token JSON text, or None when there is no usable token
    (v5.47, programme item 14) — the ONE read path for the token
    file, so no caller can accidentally parse the envelope (or,
    worse, ship it somewhere) as if it were the token.

    A protected file is unprotected here; an unprotected file is
    legacy plaintext (pre-v5.47): it is used AND rewritten
    protected via _save_token_text (first-run migration — the
    rewrite is best-effort; a read-only data dir keeps the legacy
    file working, just unmigrated). A protected file that cannot
    be unprotected returns None, exactly like a missing file."""
    from . import secrets_store

    path = _token_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — missing/unreadable = no token
        return None
    if secrets_store.looks_protected(raw.strip()):
        try:
            return secrets_store.unprotect_text(raw.strip())
        except Exception:  # noqa: BLE001 — corrupt/tampered/key lost
            return None
    try:
        _save_token_text(raw)
    except Exception:  # noqa: BLE001 — migration is best-effort
        pass
    return raw


def _save_credentials(creds) -> None:
    _save_token_text(creds.to_json())


def is_connected() -> bool:
    """True when a usable token file exists: it parses, and it either
    carries a refresh token (the normal case — access can always be
    renewed) or its access token hasn't expired yet. Pure stdlib: this
    must answer correctly even when the google libs aren't installed.
    """
    text = _read_token_text()
    if text is None:
        return False
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001 — missing/corrupt token = not connected
        return False
    if not isinstance(data, dict):
        return False
    if data.get("refresh_token"):
        return True
    expiry = data.get("expiry")
    if expiry:
        try:
            when = datetime.datetime.fromisoformat(
                str(expiry).replace("Z", "+00:00")
            )
            if when.tzinfo is None:
                when = when.replace(tzinfo=datetime.timezone.utc)
            return when > datetime.datetime.now(datetime.timezone.utc)
        except Exception:  # noqa: BLE001
            return False
    return False


def _run_flow(client_config: dict):
    """Runs the desktop OAuth consent flow and returns the credentials.

    Factored out of connect() so tests can monkeypatch just this step
    (the real one opens the user's browser and waits for consent).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    return flow.run_local_server(port=0)


def connect() -> str:
    """Connects VYUK to the user's Google account (read scopes plus
    the two narrow write scopes — see the module docstring).

    Uses the client id/secret the user saved in Settings; opens the
    system browser for Google's consent page; stores the resulting
    token at data/google_token.json.
    """
    from .config import config

    client_id = (getattr(config, "google_client_id", "") or "").strip()
    client_secret = (getattr(config, "google_client_secret", "") or "").strip()
    if not client_id or not client_secret:
        return _NO_CODES
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    try:
        creds = _run_flow(client_config)
    except ImportError:
        return _LIBS_MISSING + " (google-auth-oauthlib is missing)."
    except Exception as e:  # noqa: BLE001
        return f"Couldn't connect to Google ({_short(e)})."
    try:
        _save_credentials(creds)
    except Exception as e:  # noqa: BLE001
        return f"Google said yes, but VYUK couldn't save the sign-in ({_short(e)})."
    return _CONNECTED_OK


def disconnect() -> str:
    """Removes the local token, and asks Google to revoke it too
    (best-effort — a failed revocation never blocks the disconnect;
    the local delete is what stops VYUK)."""
    path = _token_path()
    token_value = ""
    try:
        text = _read_token_text()
        data = json.loads(text) if text is not None else None
        if isinstance(data, dict):
            token_value = data.get("refresh_token") or data.get("token") or ""
    except Exception:  # noqa: BLE001
        pass
    if token_value:
        try:
            import urllib.parse
            import urllib.request

            body = urllib.parse.urlencode({"token": token_value}).encode()
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/revoke", data=body
            )
            urllib.request.urlopen(req, timeout=3).close()
        except Exception:  # noqa: BLE001 — revocation is a nice-to-have
            pass
    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return _DISCONNECTED_OK


_credential_failure_logged = False


def _log_credential_failure(exc: Exception) -> None:
    """Surface a credential-load failure ONCE per process (v4.6,
    audit F12): the old broad except swallowed the reason whole, so
    a revoked token failed silently forever while the tools blamed
    a connection that Settings showed as healthy."""
    global _credential_failure_logged
    if _credential_failure_logged:
        return
    _credential_failure_logged = True
    print(f"[Google] stored sign-in couldn't be loaded ({_short(exc)}) "
          "— the world tools will ask the user to reconnect.")


def _credentials_from_token_text(credentials_cls, text: str):
    """Builds google-auth Credentials from unprotected token JSON
    text (v5.47, programme item 14). Prefers the library's
    from_authorized_user_info (the same parse the file variant
    performs, minus the file). Credential classes WITHOUT that
    constructor — hand-rolled test doubles are the known case —
    fall back to a short-lived temp file holding the plaintext,
    removed immediately after the call: the on-disk store stays
    protected; the temp file exists only for the duration of the
    constructor call, in the token's own directory."""
    info_loader = getattr(credentials_cls, "from_authorized_user_info",
                          None)
    if callable(info_loader):
        return info_loader(json.loads(text), SCOPES)
    import tempfile

    fd, tmp_name = tempfile.mkstemp(
        dir=str(_token_path().parent), suffix=".token.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        return credentials_cls.from_authorized_user_file(tmp_name, SCOPES)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass


def _load_credentials_detailed():
    """(credentials, state, detail) — WHY loading did or didn't work.

    States: "ok"; "not_connected" (no token file); "libs_missing"
    (the google packages aren't installed); "auth" (a token file
    exists but the sign-in was rejected — expired beyond refresh,
    revoked, or unreadable — so it needs renewing)."""
    path = _token_path()
    if not path.exists():
        return None, "not_connected", ""
    try:
        from google.oauth2.credentials import Credentials
    except ImportError as e:  # noqa: BLE001 — ModuleNotFoundError included
        return None, "libs_missing", _short(e)
    # v5.47 (programme item 14): the file on disk is a protected
    # envelope, so the credentials are built from the UNPROTECTED
    # JSON text (_read_token_text), never by handing the file to
    # google-auth. A protected file that cannot be unprotected
    # reads as no token at all — the not-connected path.
    text = _read_token_text()
    if text is None:
        return None, "not_connected", ""
    try:
        creds = _credentials_from_token_text(Credentials, text)
        if creds is not None and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request

            creds.refresh(Request())
            _save_credentials(creds)
    except ImportError as e:  # noqa: BLE001 — google.auth missing mid-refresh
        return None, "libs_missing", _short(e)
    except Exception as e:  # noqa: BLE001 — rejected / revoked / corrupt
        _log_credential_failure(e)
        return None, "auth", _short(e)
    if creds is None:
        _log_credential_failure(ValueError("stored sign-in read as empty"))
        return None, "auth", "the stored sign-in couldn't be read"
    return creds, "ok", ""


def _load_credentials():
    """Loads the stored credentials, refreshing (and re-saving) them
    when expired. Returns None when there is nothing usable."""
    creds, _state, _detail = _load_credentials_detailed()
    return creds
