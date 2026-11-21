"""Model-error-cost helpers for the Gemini Live client (skills split B46).

Moved verbatim from core/voice/live_client.py, part 1 of 2: error
classification (_error_code/_error_text/_is_model_rejection/
_is_network_error/_classify_key_error), the activity-log writer
(_log_line), and the Live cost-ledger hook (_COST_LEDGER /
_cost_record_live). The facade re-exports every name here.
"""

from __future__ import annotations

from typing import Any

# INTENTIONAL (skills split B46): imports travel with the moved family.
# DATA_DIR serves _log_line; config serves _cost_record_live
# (config.model_name); Any annotates _COST_LEDGER. Same package dir as
# the facade, so the function-local `from .. import cost_ledger` in
# _cost_record_live keeps its original meaning (core.cost_ledger).
from ..config import DATA_DIR, config


def _error_code(exc: BaseException) -> int | None:
    """Best-effort HTTP-ish status code from a google-genai error.

    The SDK's APIError carries `.code` (int) and `.message`; other
    transports expose `.status_code`. Anything else → None."""
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".lower()


def _short_exc(exc: BaseException) -> str:
    """A one-line exception summary for user-facing loss notes
    (class + message, no traceback). Field-check fix #2: the
    silent-loss notes name the failure instead of swallowing
    it, and a note must never carry a traceback."""
    text = str(exc).strip()
    return "%s: %s" % (type(exc).__name__, text) if text \
        else type(exc).__name__


def _is_model_rejection(exc: BaseException) -> bool:
    """True when Google rejected the MODEL (not the key/network):
    a 404, or the API's not-found / not-supported phrasing."""
    if _error_code(exc) == 404:
        return True
    text = _error_text(exc)
    if "not_found" in text or "not found" in text:
        return True
    if "not supported" in text or "invalid model" in text:
        return True
    return "model" in text and "404" in text


def _is_network_error(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    text = _error_text(exc)
    return any(marker in text for marker in (
        "connect", "timed out", "timeout", "getaddrinfo", "name resolution",
        "dns", "ssl", "unreachable", "network",
    ))


def _is_quota_error(exc: BaseException) -> bool:
    """True when Google refused on quota / rate-limit grounds
    (Fix 5): 429 first, then the body's own wording. Checked
    BEFORE auth: a quota body can contain the word "invalid",
    which the auth check would misread as a key problem."""
    if _error_code(exc) == 429:
        return True
    text = _error_text(exc)
    return any(marker in text for marker in (
        "429", "resource_exhausted", "resource exhausted", "quota",
        "rate limit", "rate_limit", "ratelimit",
        "too many requests",
    ))


def _classify_key_error(exc: BaseException) -> str:
    """Bucket a models.get failure for validate_api_key():
    'model' | 'quota' | 'auth' | 'network' | 'unknown'.

    Fix 5: quota is its own bucket now — before this a 429 /
    RESOURCE_EXHAUSTED refusal fell to "unknown" (or "auth"
    via the "invalid" wording), so a throttled key read as a
    broken key. Order is load-bearing: model, then quota, then
    network, then auth (see cloud_diag.classify_live_error,
    which shares this vocabulary)."""
    if _is_model_rejection(exc):
        return "model"
    if _is_quota_error(exc):
        return "quota"
    if _is_network_error(exc):
        return "network"
    code = _error_code(exc)
    text = _error_text(exc)
    key_wording = any(marker in text for marker in (
        "api key", "api_key", "unauthorized", "permission", "invalid",
    ))
    if code in (400, 401, 403) and key_wording:
        return "auth"
    if code in (401, 403):
        return "auth"
    return "unknown"


def _log_line(message: str):
    """Append one line to data/activity.log (the same file the tool
    registry logs to). Never raises."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(DATA_DIR / "activity.log", "a", encoding="utf-8") as fh:
            fh.write(f"[Live] {message}\n")
    except Exception:  # noqa: BLE001
        pass


# MN-2.8 (v5.66): Live cost-ledger hook. One row per completed
# interaction (the LISTENING transition — not per streamed
# message). The server's usageMetadata, when the model sends
# one, makes the row exact; without it the row is estimated
# with NULL tokens/cost, never a fabricated number. Record-only:
# a Live stream cannot be preflight-gated mid-audio, so cap
# enforcement for Live is the ledger's preflight API plus these
# rows feeding the cap totals that gate the discrete cloud-text
# path. Guarded: a ledger failure never breaks the receive loop.
_COST_LEDGER: Any = None


def _cost_record_live(server_content: Any, model: str | None = None) -> None:
    """One ledger row for a completed Live turn, naming the model
    that actually served it (GAPS-22, B-V5): the connected model
    the caller passes, falling back to config only when the
    session has not pinned one yet.
    """
    try:
        global _COST_LEDGER
        if _COST_LEDGER is None:
            from .. import cost_ledger as _cl  # Phase 3 V1b: moved to core/voice/

            _COST_LEDGER = _cl.CostLedger.from_env()
        usage = getattr(server_content, "usage_metadata", None)
        _COST_LEDGER.record_live_turn(model or config.model_name, usage)
    except Exception as exc:  # noqa: BLE001 — ledger failure
        # never breaks a turn. Field-check fix #2 (T7 #20): a
        # lost row means the cap totals undercount, so the loss
        # is logged and the ledger is marked degraded — the
        # Activity feed surfaces it, and the cap check can read
        # the sticky state before trusting its totals.
        _log_line(
            "Live cost row lost: the turn's spend was not "
            "recorded (%s) — the cost cap totals undercount "
            "until this is fixed." % _short_exc(exc))
        try:
            from .. import cost_ledger as _cl2  # Phase 3 V1b: moved to core/voice/

            _cl2.note_live_record_failure(exc)
        except Exception:  # noqa: BLE001 - the note itself
            pass  # must never break the session
