"""Home Assistant stored configuration (UI Phase 5, D-P5-15).

The ONE place a hub is configured (brief §4.2): the Settings "Home
hub" card reads and writes through here, and the Devices page reads
through here. Nothing else in the tree stores HA configuration.

Where each piece lives (the split the brief pins):
- Host: config (.env `HOME_ASSISTANT_HOST`, via save_env_setting) —
  an address, not a secret.
- Token: the secrets store, never config, never plaintext, never
  in .env, never in data/. The token envelope (whole-file
  protect_text, the google_services_token precedent) lives beside
  the machine key, in the secrets store's key dir — deliberately
  NOT data/ (the secrets_store module docstring's rule). If the
  store reports no protecting backend, the token is NOT persisted:
  save_ha_token refuses and says so plainly, and the token is
  held in memory for that session only.
- Allowlist: config (.env `HOME_ASSISTANT_ALLOWLIST`, compact JSON
  `{"allowlist": {entity_id: room}, "assist_exposed": [...]}`) —
  entity ids and room names, not secrets. Managed on the same card,
  saved by the same Test & save.

No network call is ever made here: validation is the LAN gate +
token presence (HomeAssistantConfig.__post_init__), and the live
health() check is the Settings card's job, not this module's.

Test seam: the .env write goes through this module's
`save_env_setting` attribute (imported from core.config), so a
suite can substitute a fake instead of touching the tree's .env;
the token file follows secrets_store.set_key_dir, so a suite can
point it at a temp dir. The token itself is never logged and never
appears in any return value or repr.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import save_env_setting
from . import reach
from . import secrets_store

_HOST_ENV_KEY = "HOME_ASSISTANT_HOST"
_ALLOWLIST_ENV_KEY = "HOME_ASSISTANT_ALLOWLIST"
_TOKEN_FILENAME = ".vyuk_home_assistant_token"

# The §4.4 error mapping (brief §1.4): every HomeAssistantError
# kind to the plain-words line the Devices page shows, each naming
# what to do next. Never a raw traceback.
_ERROR_LINES = {
    "config": "Check the address and token in Settings — the hub "
              "address or token isn't usable as given.",
    "refused": "Not allowed for this device — it isn't on the "
               "allowlist in Settings, or isn't exposed to Assist.",
    "auth": "The hub rejected the token — check it in Settings.",
    "not_found": "The hub has no such device.",
    "timeout": "The hub didn't answer — check it is on and on "
               "this network.",
    "transport": "Couldn't reach the hub — check it is on and on "
                 "this network.",
    "server": "The hub answered with an error — try again, and "
              "check the hub itself if it keeps failing.",
}


def ha_error_line(error) -> str:
    """The plain-words line for a Home Assistant failure (an
    HomeAssistantError, or a kind string). Unknown kinds read as
    a transport failure, never a traceback."""
    kind = getattr(error, "kind", None)
    if kind is None:
        kind = str(error or "")
    return _ERROR_LINES.get(kind, _ERROR_LINES["transport"])


def _token_path() -> Path:
    """The token envelope file: beside the machine key, in the
    secrets store's key dir (which follows VYUK_SECRET_KEY_DIR /
    set_key_dir, so tests can point it at a temp dir). The import
    is private on purpose — this is the same package's key
    directory, and duplicating its resolution here would drift."""
    return secrets_store._key_dir() / _TOKEN_FILENAME  # noqa: SLF001


def load_ha_host() -> str:
    """The stored hub host ("" when none is configured)."""
    return (os.environ.get(_HOST_ENV_KEY) or "").strip()


def save_ha_host(host: str) -> bool:
    """Persist the hub host ("" clears it — the hub is then
    unconfigured). True when it landed. An address, not a
    secret, so it lives in .env beside the allowlist."""
    try:
        save_env_setting(_HOST_ENV_KEY, str(host or "").strip())
    except Exception:  # noqa: BLE001 — a failed save reads as False
        return False
    return True


def load_ha_allowlist() -> tuple[dict, set]:
    """(allowlist, assist_exposed): {entity_id: room} and the set
    of Assist-exposed entity ids. Missing / corrupt / wrong-type
    reads as empty — never a raise, never a guessed entry."""
    raw = (os.environ.get(_ALLOWLIST_ENV_KEY) or "").strip()
    if not raw:
        return {}, set()
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}, set()
    if not isinstance(data, dict):
        return {}, set()
    allow = data.get("allowlist")
    if not isinstance(allow, dict):
        allow = {}
    allow = {str(k): str(v) for k, v in allow.items()
             if str(k).strip() and str(v).strip()}
    exposed = data.get("assist_exposed")
    if not isinstance(exposed, list):
        exposed = []
    return allow, {str(e) for e in exposed if str(e).strip()}


def save_ha_allowlist(allowlist: dict, assist_exposed) -> bool:
    """Persist the allowlist + Assist-exposed set as one compact
    JSON value in .env. True when it landed."""
    payload = {
        "allowlist": {str(k): str(v) for k, v in dict(
            allowlist or {}).items()},
        "assist_exposed": sorted({str(e) for e in (
            assist_exposed or set())}),
    }
    try:
        save_env_setting(_ALLOWLIST_ENV_KEY,
                         json.dumps(payload, separators=(",", ":")))
    except Exception:  # noqa: BLE001 — a failed save reads as False
        return False
    return True


def load_ha_token() -> tuple[str | None, str]:
    """(token, status): the stored token, or None with status
    "missing" (no token stored) or "unreadable" (an envelope is
    there but will not open — re-enter the token). The token
    itself is the only value ever returned; it is never logged."""
    path = _token_path()
    try:
        envelope = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None, "missing"
    if not envelope:
        return None, "missing"
    try:
        return secrets_store.unprotect_text(envelope), "ok"
    except Exception:  # noqa: BLE001 — a bad envelope reads unreadable
        return None, "unreadable"


def save_ha_token(token: str) -> tuple[bool, str]:
    """Persist the token through the secrets store. Refuses (False)
    when the store reports no protecting backend — a token is never
    written where it would sit in plaintext. (ok, message); the
    message never contains the token."""
    token = str(token or "")
    if not token:
        return False, "A token is needed — paste the long-lived access token."
    if secrets_store.active_backend_name() == "none-plaintext":
        return False, ("No protecting store is available on this machine, "
                       "so the token can't be saved — it will be held in "
                       "memory for this session only, and asked for again "
                       "next launch.")
    try:
        envelope = secrets_store.protect_text(token)
    except Exception:  # noqa: BLE001 — protection failing refuses, too
        return False, ("The token couldn't be protected for storage — "
                       "nothing was saved.")
    path = _token_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(envelope)
            try:
                os.chmod(tmp_name, 0o600)
            except OSError:  # noqa: BLE001 — best effort on Windows
                pass
            os.replace(tmp_name, path)
        except Exception:  # noqa: BLE001
            try:
                os.unlink(tmp_name)
            except OSError:  # noqa: BLE001 — best effort cleanup
                pass
            raise
        try:
            os.chmod(path, 0o600)
        except OSError:  # noqa: BLE001 — best effort on Windows
            pass
    except Exception:  # noqa: BLE001
        return False, ("The token couldn't be saved — check the settings "
                       "can be written, then try again.")
    return True, "Token saved."


def clear_ha_token_file() -> None:
    """Delete the stored token envelope. Never raises (a missing
    file is already clear)."""
    try:
        _token_path().unlink(missing_ok=True)
    except OSError:  # noqa: BLE001 — best effort
        pass


def load_ha_config() -> dict:
    """The stored hub configuration, as the Devices page and the
    Settings card read it. No network call, ever. `configured`
    is True only when a host AND a readable token are stored."""
    host = load_ha_host()
    token, token_status = load_ha_token()
    allowlist, exposed = load_ha_allowlist()
    return {
        "host": host,
        "has_token": token is not None,
        "token_status": token_status,
        "allowlist": allowlist,
        "assist_exposed": exposed,
        "configured": bool(host) and token is not None,
    }


def build_ha_client_for(host, token, allowlist=None,
                        assist_exposed=None):
    """(client, reason): a HomeAssistantClient for the given
    host/token (entered on the Settings card, not yet saved),
    or (None, reason) when the values refuse. Constructing the
    config validates LAN + token presence with no network call;
    the client itself opens no connection until used. The
    Settings card tests entered values through here so a failed
    test saves nothing (S-P5-03)."""
    from . import homeassistant as _ha

    host = str(host or "").strip()
    token = str(token or "")
    if not host:
        return None, "no Home Assistant instance configured"
    if not token:
        return None, "no Home Assistant token stored"
    try:
        config = _ha.HomeAssistantConfig(host=host, token=token)
    except _ha.HomeAssistantError as err:
        return None, ha_error_line(err)
    scope = reach.HomeAssistantScope(
        expose_new=False, allowlist=dict(allowlist or {}),
        assist_exposed=set(assist_exposed or set()))
    return _ha.HomeAssistantClient(config, scope=scope), ""


def build_ha_client():
    """(client, reason): a HomeAssistantClient built from the
    stored config, or (None, reason) when nothing is configured
    or the stored config refuses. Constructing the config
    validates LAN + token presence with no network call; the
    client itself opens no connection until used."""
    host = load_ha_host()
    if not host:
        return None, "no Home Assistant instance configured"
    token, token_status = load_ha_token()
    if token is None:
        if token_status == "unreadable":
            return None, ("the stored token couldn't be read — "
                          "re-enter it in Settings")
        return None, "no Home Assistant token stored"
    allowlist, exposed = load_ha_allowlist()
    return build_ha_client_for(host, token, allowlist, exposed)


def ha_config_status() -> dict:
    """The honest configuration status (home_assistant_status,
    no network ever): configured only when a host and a readable
    token are stored; the host is named, the token never is. The
    host is validated through the LAN gate directly — no config
    object is ever built with a placeholder token."""
    from . import homeassistant as _ha

    cfg = load_ha_config()
    if not cfg["configured"]:
        if not cfg["host"]:
            return {
                "configured": False,
                "reason": "no Home Assistant instance configured",
                "host": None,
            }
        if cfg["token_status"] == "unreadable":
            return {
                "configured": False,
                "reason": "the stored token couldn't be read — "
                          "re-enter it in Settings",
                "host": cfg["host"],
            }
        return {
            "configured": False,
            "reason": "no Home Assistant token stored",
            "host": cfg["host"],
        }
    lan = reach.is_lan_target(cfg["host"])
    if not lan.ok:
        return {"configured": False,
                "reason": _ERROR_LINES["config"],
                "host": cfg["host"]}
    token, _status = load_ha_token()
    config = _ha.HomeAssistantConfig(host=cfg["host"], token=token)
    return _ha.home_assistant_status(config)
