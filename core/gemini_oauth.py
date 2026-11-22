"""Gemini sign-in — "Continue with Google" (round-3 OAuth build, 2026-11-21).

The first-run wizard no longer asks for a Gemini API key. It offers one
"Continue with Google" button running the standard installed-app OAuth
flow against VYUK's OWN Desktop-app OAuth client (project vyuk-515320).
VYUK stores the access + refresh tokens locally and calls the Gemini
API with Authorization: Bearer instead of an API key. Quota stays on
the user's own Google account — the same free tier as a key.

PROVEN BY THE SPIKE (2026-11-21, SPIKE-EXIT 0; see
r3oauth_signin_work/spike_oauth_paste.py):
  1. The scope set is exactly these six — request all six or the
     exchange fails. The bare generative-language scope is NOT a
     valid OAuth scope (Google Error 400 invalid_scope). Never
     request cloud-platform.
  2. OAUTHLIB_RELAX_TOKEN_SCOPE=1 must be set in-process BEFORE the
     flow runs (Google normalises the userinfo aliases between the
     authorization and token responses).
  3. flow.authorization_url() is called exactly ONCE, inside
     run_local_server() — never separately first.
  4. google-genai does NOT accept OAuth credentials for the Gemini
     Developer API (genai.Client(credentials=creds) raises "No API
     key was provided"; credentials are honoured only for Vertex
     AI). DO NOT route through Vertex AI. The Bearer token is wired
     in by the header swap in build_bearer_client() — proven by a
     real live-session call on both SDK generations (see the probe
     in r3oauth_build_work/probe_bearer_live.py: PROBE-EXIT 0 on
     google-genai 2.33.0 and 3.6.1, REST + a live turn on
     gemini-3.8-live).

REUSE: token storage, refresh-on-load and revoke-on-disconnect are
core/google_services_token.py's parameterized machinery (the same
code Calendar/Gmail use) — separate scope set, separate token file
(data/gemini_token.json, same secrets_store protected envelope).
The spike token file (r3oauth_signin_work/spike_token.json) is spike
evidence, NEVER product state — this module never reads it.

Tokens are NEVER printed or logged. The token file is 0600-equivalent
(protected envelope, atomic tmp + os.replace write — see
google_services_token._save_token_text).
"""

from __future__ import annotations

import base64
import json

from . import google_services_token as _token

# Spike finding 1: exactly these six scopes. The bare
# generative-language scope is invalid (Google 400 invalid_scope);
# cloud-platform is NEVER requested.
GEMINI_SCOPES = [
    "https://www.googleapis.com/auth/generative-language.retriever",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid",
    "email",
    "profile",
]

GEMINI_TOKEN_FILENAME = "gemini_token.json"

# The SDK's default api_version moved: 2.33.0 defaults to v1beta,
# 3.6.1 defaults to v1 — and the v1 Live websocket endpoint 404s
# (proven by probe_ws_v1.py / probe_live_404.py). Both client
# factories pin v1beta explicitly, for BOTH auth paths.
LIVE_API_VERSION = "v1beta"

# The api_key the Bearer client is constructed with. It is a
# placeholder, never sent: build_bearer_client() removes the
# x-goog-api-key header it produces before the client is used —
# a dummy key header alongside a valid Bearer returns 400
# (proven by probe_bearer_plus_key.py), so the removal is load-
# bearing, not cosmetic. The value is deliberately recognisable
# in a debugger and is NOT a real key.
_BEARER_PLACEHOLDER = "OAUTH-BEARER"

_CONNECTED_OK = (
    "Signed in with Google — VYUK will think with your Google "
    "account. You can sign out any time in Settings."
)

_DISCONNECTED_OK = "Signed out — VYUK no longer has access to your Google account."

_NO_CODES = (
    "VYUK's Google sign-in isn't set up in this build — the sign-in "
    "codes weren't included. (Release packaging injects them; see "
    "the OAuth build report.)"
)


def _client_config():
    """The installed-app client config for VYUK's own Desktop-app
    OAuth client (project vyuk-515320). The id/secret come from
    config (env GEMINI_OAUTH_CLIENT_ID / GEMINI_OAUTH_CLIENT_SECRET,
    secret-flagged) — the release packaging injects them; the repo
    must NEVER contain the secret. Returns None when either is
    missing."""
    from .config import config

    client_id = (getattr(config, "gemini_oauth_client_id", "") or "").strip()
    client_secret = (
        getattr(config, "gemini_oauth_client_secret", "") or ""
    ).strip()
    if not client_id or not client_secret:
        return None
    return {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def _run_flow(client_config: dict):
    """Runs the desktop OAuth consent flow and returns the credentials.

    Factored out of connect() so tests can monkeypatch just this step
    (the real one opens the user's browser and waits for consent).
    Delegates to google_services_token._run_flow, which sets
    OAUTHLIB_RELAX_TOKEN_SCOPE=1 first and calls authorization_url()
    exactly once, inside run_local_server() (spike findings 2 and 3).
    """
    return _token._run_flow(client_config, GEMINI_SCOPES)


def connect() -> str:
    """Runs the "Continue with Google" flow and stores the token.

    Opens the system browser for Google's consent page; stores the
    resulting token at data/gemini_token.json (protected envelope).
    Returns a plain-language status line for the wizard/Settings.
    """
    client_config = _client_config()
    if client_config is None:
        return _NO_CODES
    try:
        creds = _run_flow(client_config)
    except ImportError:
        return _token._LIBS_MISSING + " (google-auth-oauthlib is missing)."
    except Exception as e:  # noqa: BLE001
        return f"Couldn't sign in with Google ({_token._short(e)})."
    try:
        _token._save_credentials(creds, GEMINI_TOKEN_FILENAME)
    except Exception as e:  # noqa: BLE001
        return (
            "Google said yes, but VYUK couldn't save the sign-in "
            f"({_token._short(e)})."
        )
    return _CONNECTED_OK


def disconnect() -> str:
    """Removes the local Gemini token, and asks Google to revoke it
    too (best-effort — a failed revocation never blocks the
    disconnect; the local delete is what stops VYUK)."""
    return _token.disconnect(GEMINI_TOKEN_FILENAME, _DISCONNECTED_OK)


def is_signed_in() -> bool:
    """True when a usable Gemini token file exists (parses, and
    either carries a refresh token or its access token hasn't
    expired yet). Pure stdlib — answers correctly even when the
    google libs aren't installed."""
    return _token.is_connected(GEMINI_TOKEN_FILENAME)


def get_credentials():
    """Loads the stored Gemini credentials, refreshing (and
    re-saving) them when expired. Returns None when there is
    nothing usable."""
    return _token._load_credentials(GEMINI_TOKEN_FILENAME, GEMINI_SCOPES)


def get_access_token() -> str | None:
    """The current access token (refreshing first when expired),
    or None when there is no usable sign-in. The value is NEVER
    logged — callers pass it straight into a header."""
    creds = get_credentials()
    if creds is None:
        return None
    return getattr(creds, "token", None) or None


def signed_in_email() -> str | None:
    """The Google account address, for the "Signed in as …" line
    only. First the stored id_token's email claim (base64 decoded
    WITHOUT signature verification — display only, never used for
    any auth decision). The real flow's saved token carries NO
    id_token (proven by the spike: creds.to_json() has none), so
    the fallback is the userinfo endpoint (GET
    https://openidconnect.googleapis.com/v1/userinfo with the
    access token — a granted scope), cached per refresh token so
    the network call happens at most once per sign-in. Returns
    None when neither yields an address."""
    try:
        text = _token._read_token_text(GEMINI_TOKEN_FILENAME)
        if text is None:
            return None
        data = json.loads(text)
        id_token = data.get("id_token") or ""
        parts = str(id_token).split(".")
        if len(parts) == 3:
            payload = parts[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            email = claims.get("email") if isinstance(claims, dict) else None
            if email:
                return email
        refresh = data.get("refresh_token") or ""
        cached = _EMAIL_CACHE.get("refresh_token")
        if cached is not None and cached == refresh:
            return _EMAIL_CACHE.get("email")
        token = get_access_token()  # refreshes first when expired
        if not token:
            return None
        import urllib.request

        req = urllib.request.Request(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        email = info.get("email") if isinstance(info, dict) else None
        _EMAIL_CACHE["refresh_token"] = refresh
        _EMAIL_CACHE["email"] = email or None
        return email or None
    except Exception:  # noqa: BLE001 — no id_token/email = no address line
        return None


# In-process cache for the userinfo fallback above (see
# signed_in_email): one network fetch per refresh token.
_EMAIL_CACHE: dict = {}


def resolve_auth():
    """The live session's auth decision, in priority order.

    Returns ("oauth", creds) when a usable sign-in exists;
    ("oauth_expired", None) when a token file exists but the
    sign-in is unusable (expired beyond refresh, revoked, or
    unreadable) — the caller reports "sign-in expired", NOT
    "no API key"; ("key", api_key) for the legacy key path;
    (None, None) when neither is available."""
    if is_signed_in():
        creds = get_credentials()
        if creds is not None:
            return ("oauth", creds)
        return ("oauth_expired", None)
    from .config import config

    key = (getattr(config, "api_key", "") or "").strip()
    if key:
        return ("key", key)
    return (None, None)


def build_bearer_client(creds=None):
    """Builds a google-genai client authenticated with the OAuth
    Bearer token (the header swap, proven by probe_bearer_live.py).

    genai.Client is constructed with a placeholder api_key and an
    explicit v1beta api_version; the x-goog-api-key header the
    placeholder produces is then REMOVED and Authorization: Bearer
    is added in its place. The swap is verified before the client
    is returned — a client that still carried the dummy key header
    would fail every call with 400, so a failed swap raises
    instead of shipping a broken client. The same client serves
    REST (models.get) and Live (the Live connect copies
    http_options.headers verbatim into the websocket handshake).
    """
    from google import genai
    from google.genai import types

    if creds is None:
        creds = get_credentials()
    if creds is None:
        raise ValueError("Not signed in with Google — no OAuth token to use.")
    token = getattr(creds, "token", None)
    if not token:
        raise ValueError("The stored Google sign-in has no access token.")
    client = genai.Client(
        api_key=_BEARER_PLACEHOLDER,
        http_options=types.HttpOptions(api_version=LIVE_API_VERSION),
    )
    headers = client._api_client._http_options.headers
    headers.pop("x-goog-api-key", None)
    headers["Authorization"] = f"Bearer {token}"
    if "x-goog-api-key" in headers or "Authorization" not in headers:
        raise RuntimeError("The Bearer header swap did not take effect.")
    return client


def build_key_client(api_key: str):
    """Builds a google-genai client authenticated with a legacy API
    key — the same construction as before, plus the explicit v1beta
    api_version (the SDK's default moved to v1 in 3.6.1, and the
    v1 Live websocket endpoint 404s)."""
    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(api_version=LIVE_API_VERSION),
    )


def build_client_for_auth(auth_kind, auth_value):
    """The Gemini client for a resolved auth method — the Bearer
    header-swap client for ("oauth", creds), the key client
    (explicit v1beta) for ("key", api_key). The live session calls
    this at both client-build sites (the first build and the
    proxy retry), so the two sites can never disagree about auth.
    Callers resolve fresh credentials first (resolve_auth refreshes
    on load); this function uses the credentials as given."""
    if auth_kind == "oauth":
        return build_bearer_client(auth_value)
    if auth_kind == "key":
        return build_key_client(auth_value)
    raise ValueError(f"Unknown auth method: {auth_kind!r}")


def validate_token(model=None) -> tuple[bool, str, str]:
    """Cheapest real validation of the stored Google sign-in.

    One metadata GET (client.models.get) proves the token
    authenticates AND the model exists, without opening a Live
    session; the returned Model's supported_actions then answers
    the Live door (cloud_diag.live_support_from_model — an absent
    list is "unknown", never a failure). Returns (ok, kind,
    message) with kind one of: "ok" | "auth" | "model" | "quota" |
    "network" | "library" | "unknown" — the same contract as
    live_client_helpers.validate_api_key, so the two doors report
    failures in the same words. Sync — call it off the UI thread.
    """
    from .config import config
    from .voice.live_client_error_cost import _classify_key_error
    from .voice.cloud_diag import live_support_from_model

    if model is None:
        model = getattr(config, "model_name", "") or "gemini-3.8-live"
    if not is_signed_in():
        return (False, "auth", "Not signed in with Google yet.")
    try:
        from google import genai  # noqa: F401 — presence check only
    except ImportError:
        return (False, "library",
                "The Google library (google-genai) isn't installed in this build.")
    try:
        client = build_bearer_client()
        info = client.models.get(model=model)
    except Exception as exc:  # noqa: BLE001
        if not get_credentials():
            return (False, "auth",
                    "The Google sign-in has expired or was revoked — "
                    "sign in again.")
        return (False, _classify_key_error(exc), str(exc)[:300])
    live = live_support_from_model(info)
    if live == "not_supported":
        return (False, "model",
                f"Model '{model}' doesn't support live voice "
                "(no bidiGenerateContent).")
    return (True, "ok", f"Model '{model}' is available.")
