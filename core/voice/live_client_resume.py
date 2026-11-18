from __future__ import annotations

import time

from ..config import DATA_DIR, config

try:
    from google import genai
    from google.genai import types
except Exception:  # noqa: BLE001 — a build without google-genai must
    # still import: the local brain keeps working, and cloud paths
    # report the missing library honestly instead of crashing.
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]

# INTENTIONAL (skills split B47): resume + context-compression family
# moved from live_client.py. Imports _error_text from the B46
# error-cost module (the B46 out-edge: resume resolves _error_text x4).
from .live_client_error_cost import _error_text


# The Live model ids Google currently offers (Oct 2026). The original
# default, gemini-2.0-flash-exp, was retired with the whole 2.0 Flash
# line and is rejected at the handshake — which is why cloud Connect
# used to fail for every user regardless of their key. start() tries
# the configured model first, then these, and persists the winner.
KNOWN_LIVE_MODELS = (
    # Order is field evidence + the calendar (v5.54, Wave 0 —
    # the wave-32 stay path): "gemini-3.8-live" (GA, Sep 2026)
    # leads. Google retires the 2.5 native-audio PREVIEW
    # ids on 17 Nov 2026 with no redirect, so v5.95 removes
    # them from the chain AHEAD of that date (gate decision):
    # a fallback must never land on a retired id. The
    # persisted-winner rule records whichever candidate
    # actually connects.
    # "gemini-live-2.5-flash-native-audio" is rejected by Google
    # for the Live API (1008: not found for API version v1beta,
    # not supported for bidiGenerateContent) on Developer keys;
    # it stays — it may be valid for other keys/API versions.
    "gemini-3.8-live",
    "gemini-live-2.5-flash-native-audio",
)


CONNECT_FIRST_TIMEOUT_S = 25.0


CONNECT_LATER_TIMEOUT_S = 10.0


# --- Live session resumption + context compression (v5.45, B9) --------
# Long Live conversations used to die at the API's context limit
# and restart cold. Now: (1) context-window compression is
# enabled on every connect (a sliding window summarises early
# turns server-side once the trigger token count is passed);
# (2) the server-issued resumption handle is persisted under
# data/live_resume.json the moment it arrives, and a reconnect
# tries RESUME before cold start. Any resumption failure falls
# back to a cold start for that candidate, silently — a rejected
# handle is cleared and never poisons the next candidate. A
# clean close (stop()) clears the handle too: a deliberately
# ended session is not resumed.
#
# v5.56 (Wave 2, RS-1) additions to that B9 shape:
#   * HANDLE WINDOW — 10 minutes. The plan's sources conflict
#     (24 h in one doc, ~10 min in another); RS-1 designs for
#     the SHORTER window, because offering a handle the server
#     has already forgotten costs a failed resume attempt and
#     a cold retry, while discarding a handle the server would
#     still have honoured costs only a cold start — and the
#     cold path is already the safe fallback. A handle older
#     than RESUME_HANDLE_TTL_S is treated as stale: _load
#     clears it and returns None, so start() connects cold.
#     Handles carry saved_epoch (time.time()) from this
#     version on; a file with no provable age (legacy
#     saved_at-only, or unparseable) is likewise treated as
#     stale and cleared — age that cannot be proven inside
#     the window is not assumed inside it.
#   * MODE GATE — see _resume_config_for_mode: Enterprise
#     clients offer handle + transparent=True; Developer
#     clients offer the handle WITHOUT transparent (v5.51
#     item 13: the Developer API rejects the transparent
#     parameter outright). What a Developer path NEVER sends
#     is transparent=True; the handle itself IS carried
#     handle-only (v5.51's pinned behaviour — see the
#     _resume_config_for_mode note on the wording).
#   * STALE HANDLE (1008) — see _is_stale_handle_close: a
#     resumed connect rejected with WebSocket close code 1008
#     clears the handle and retries cold (the B9 any-failure
#     path, now named for this case), and a mid-session 1008
#     close in _receive_loop clears it too, so the GUI's
#     auto-reconnect does not re-present a handle the server
#     just refused.
#   * goAway — handled in _receive_loop (flag + LIVE_GOAWAY
#     notice + session end for a resume reconnect), never
#     depended on: the 7 s watchdog above is the primary trip.
RESUME_HANDLE_FILE = "live_resume.json"


RESUME_HANDLE_TTL_S = 600.0


COMPRESSION_TRIGGER_TOKENS = 80_000


COMPRESSION_TARGET_TOKENS = 20_000


_resume_path_override = None  # tests point this at a temp file


def _resume_path():
    if _resume_path_override is not None:
        return _resume_path_override
    return DATA_DIR / RESUME_HANDLE_FILE


def _resume_handle_age_s(data: dict) -> float | None:
    """Age in seconds of a persisted handle record, or None
    when the age cannot be proven. saved_epoch (v5.56) is
    authoritative; a legacy saved_at wall-clock string is
    parsed as local time as a fallback. A negative age (clock
    moved backwards) reads as fresh, not stale."""
    epoch = data.get("saved_epoch")
    if isinstance(epoch, (int, float)) and epoch > 0:
        return time.time() - float(epoch)
    saved_at = data.get("saved_at")
    if isinstance(saved_at, str):
        try:
            stamp = time.mktime(
                time.strptime(saved_at, "%Y-%m-%dT%H:%M:%S"))
            return time.time() - stamp
        except Exception:  # noqa: BLE001 — unparseable age
            return None
    return None


def _load_resume_handle() -> str | None:
    """The persisted resumption handle, or None. A corrupt or
    shapeless file reads as no handle (and is left alone).
    RS-1 (v5.56): a handle older than RESUME_HANDLE_TTL_S
    (10 min) — or one whose age cannot be proven — reads as
    no handle AND is cleared, so a stale handle is never
    offered to the server and never lingers for the next
    connect to trip over."""
    try:
        import json as _json

        data = _json.loads(_resume_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        handle = data.get("handle")
        if not handle:
            return None
        age = _resume_handle_age_s(data)
        if age is None or age > RESUME_HANDLE_TTL_S:
            _clear_resume_handle()
            return None
        return str(handle)
    except Exception:  # noqa: BLE001
        return None


def _save_resume_handle(handle: str) -> None:
    """Persist a freshly-issued handle (written on receipt).
    v5.56: saved_epoch (time.time()) rides beside the human
    readable saved_at so _load can enforce the 10-min window
    without trusting a wall-clock string alone."""
    if not handle:
        return
    try:
        import json as _json

        path = _resume_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(_json.dumps(
            {"handle": str(handle),
             "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "saved_epoch": time.time()}),
            encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001 — resumption is a nicety
        pass


def _clear_resume_handle() -> None:
    """Drop the persisted handle (clean close / invalidation)."""
    try:
        _resume_path().unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass


def _client_is_enterprise(client) -> bool:
    """True when `client` is a Gemini Enterprise Agent Platform
    (Vertex AI) client; False for a Developer API client (api_key
    auth) — and False whenever the mode cannot be determined.

    google-genai exposes the mode on the client: an api_key client
    reports vertexai=False (its api client carries vertexai=None);
    a vertexai=True client reports True. Developer is the safe
    default: omitting `transparent` costs nothing on Enterprise,
    while sending it to the Developer API fails the whole connect
    (v5.51 item 13, field report 2026-10-23)."""
    for obj in (client, getattr(client, "_api_client", None)):
        if obj is None:
            continue
        flag = getattr(obj, "vertexai", None)
        if flag is not None:
            return bool(flag)
    return False


def _build_session_resumption(handle: str | None = None,
                              transparent: bool = True):
    """types.SessionResumptionConfig for the installed
    google-genai (2.32: fields handle + transparent), or None
    when the library predates it — fail soft, connect proceeds
    exactly as before. transparent=True asks the server to issue
    handles without interrupting the conversation.

    v5.51 (item 13): transparent resumption is an Enterprise-only
    capability — the Developer API rejects the parameter outright
    and the connect dies with it ("transparent parameter is only
    supported in Gemini Enterprise Agent Platform mode...").
    Callers pass transparent=False for Developer-mode clients and
    the config is then built handle-only: the parameter is left
    unset, never sent."""
    try:
        sr_type = getattr(types, "SessionResumptionConfig", None)
        if sr_type is None:
            return None
        if transparent:
            return sr_type(handle=handle or None, transparent=True)
        return sr_type(handle=handle or None)
    except Exception:  # noqa: BLE001
        return None


def _resume_config_for_mode(handle: str | None,
                            enterprise_mode: bool):
    """RS-1 (v5.56): THE mode branch for resumption config —
    the single seam start() builds through, so no connect path
    can offer a mode-wrong config by accident.

    Enterprise (Vertex AI) -> handle + transparent=True: the
    server issues fresh handles and reports the last consumed
    client message index for transparent reconnection.
    Developer (api_key) -> handle WITHOUT transparent: the
    handle is carried handle-only, transparent left unset
    (absent from the wire dump). v5.51 item 13 proved the
    Developer API rejects transparent itself, and pinned the
    Developer config as handle-carrying; that is what this
    branch preserves.

    WORDING FLAG (for the Wave 2 coordinator's ruling): the
    RS-1 brief's last mode sentence can be read literally as
    "a Developer path that sends ANY resume handle is a
    defect". That literal reading is NOT implemented here,
    deliberately: it would delete B9 resume-first for the
    user's own Developer key and contradicts the pinned
    v5.51 behaviour (test_v551_livefix §B(a): "Developer
    config carries the handle"). What the Developer branch
    never sends is transparent=True — the Enterprise-only
    parameter, which is what the plan row itself names
    ("transparent resumption is Enterprise-only; developer
    keys never send it"). If the coordinator rules the
    literal reading, the change is one line: this branch's
    Developer arm returns _build_session_resumption(
    None, transparent=False) instead."""
    return _build_session_resumption(
        handle, transparent=bool(enterprise_mode))


def _close_code_of(exc: BaseException) -> int | None:
    """Best-effort WebSocket close code from an error: the
    SDK's .code/.status_code/.status first, then a
    websockets ConnectionClosed's rcvd/sent close codes.
    Anything else -> None (callers also try the text)."""
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    for side in ("rcvd", "sent"):
        frame = getattr(exc, side, None)
        value = getattr(frame, "code", None) if frame is not None else None
        if isinstance(value, int):
            return value
    return None


def _is_stale_handle_close(exc: BaseException) -> bool:
    """True when an error IS a 1008 close — the server's
    policy-violation close, which is how a stale/expired
    resumption handle is refused at reconnect. RS-1 names
    this case explicitly (B9's any-failure cold retry already
    handled it silently); the branch in _try_candidates logs
    it as stale, and _receive_loop clears the handle on a
    mid-session 1008 so the reconnect starts cold instead of
    re-presenting a refused handle."""
    if _close_code_of(exc) == 1008:
        return True
    return "1008" in _error_text(exc)


def _is_already_connected_close(exc: BaseException) -> bool:
    """True when the server refuses because a session is ALREADY
    connected for the offered handle — LIVEFIX, distinct from a
    stale/expired handle. The handle itself is valid; the server
    still holds the session it names (typically after an abnormal
    1006 close the server has not reaped yet), so resuming
    collides with it. Phrase-based, code-independent: the field
    error carries both the 1008 code and this phrase, so a
    stale-first check would misname it — callers check this
    BEFORE _is_stale_handle_close, and never log this case as
    stale-handle."""
    text = _error_text(exc)
    return "already connected" in text or "existing client" in text


def _build_context_window_compression():
    """types.ContextWindowCompressionConfig (2.32: trigger_tokens
    + sliding_window{target_tokens}), or None — fail soft."""
    try:
        cwc_type = getattr(types, "ContextWindowCompressionConfig", None)
        sw_type = getattr(types, "SlidingWindow", None)
        if cwc_type is None or sw_type is None:
            return None
        return cwc_type(
            trigger_tokens=COMPRESSION_TRIGGER_TOKENS,
            sliding_window=sw_type(target_tokens=COMPRESSION_TARGET_TOKENS))
    except Exception:  # noqa: BLE001
        return None


def _config_resume_handle(session_config) -> str | None:
    sr = getattr(session_config, "session_resumption", None)
    handle = getattr(sr, "handle", None) if sr is not None else None
    return str(handle) if handle else None


def _config_transparent(session_config) -> bool:
    """True when the config's resumption config asks for
    transparent handle issuance (v5.51 item 13)."""
    sr = getattr(session_config, "session_resumption", None)
    return bool(getattr(sr, "transparent", None)) if sr is not None else False


def _swap_resumption(session_config, fresh):
    """A copy of `session_config` carrying `fresh` as its
    session_resumption (model_copy, with a shallow-copy fallback
    for config doubles that are not pydantic models)."""
    try:
        return session_config.model_copy(
            update={"session_resumption": fresh})
    except Exception:  # noqa: BLE001
        pass
    try:
        import copy as _copy

        clone = _copy.copy(session_config)
        clone.session_resumption = fresh
        return clone
    except Exception:  # noqa: BLE001
        return session_config


def _without_resume_handle(session_config, transparent=None):
    """A copy of `session_config` whose resumption config drops
    the stored handle (the cold-start retry after a resumption
    failure). The replacement preserves the transparent choice
    the incoming config carries — that choice IS the API mode's,
    made when start() built the config (v5.51 item 13) — unless
    `transparent` is passed explicitly. A config with no
    resumption at all keeps the historical default."""
    if transparent is None:
        sr = getattr(session_config, "session_resumption", None)
        transparent = (bool(getattr(sr, "transparent", None))
                       if sr is not None else True)
    fresh = _build_session_resumption(None, transparent=transparent)
    return _swap_resumption(session_config, fresh)


def _strip_cleared_handle(session_config):
    """A pass-entry copy of `session_config` that drops a resumption
    handle the store no longer holds (LIVEFIX). start()'s proxy
    retry pass reuses its config, so a handle cleared during the
    first pass would otherwise be presented again — the file is
    the truth, the in-memory config is stale. When the file still
    holds the offered handle (or no handle was offered), the config
    is returned unchanged. A handle that aged out between passes is
    likewise stripped: _load clears it, so it is no longer held."""
    offered = _config_resume_handle(session_config)
    if offered is None:
        return session_config
    if _load_resume_handle() == offered:
        return session_config
    return _without_resume_handle(session_config)


def _without_transparent(session_config):
    """A copy of `session_config` whose resumption config keeps
    its handle but drops the `transparent` parameter (v5.51
    item 13: the one retry after the API rejects that parameter
    itself)."""
    fresh = _build_session_resumption(
        _config_resume_handle(session_config), transparent=False)
    return _swap_resumption(session_config, fresh)


def _is_resumption_rejection(exc: BaseException) -> bool:
    """True when a connect error names the resumption handle —
    diagnostic helper; the fail-soft retry in _try_candidates
    actually triggers on ANY failure of a resumed attempt."""
    text = _error_text(exc)
    return any(marker in text for marker in (
        "resumption", "resume", "session handle", "handle invalid",
        "handle expired", "invalid handle",
    ))


def _is_transparent_rejection(exc: BaseException) -> bool:
    """True when a connect error names the `transparent`
    resumption parameter itself — the Developer API's verbatim
    wording is "transparent parameter is only supported in
    Gemini Enterprise Agent Platform mode, not in Gemini
    Developer API mode" (v5.51 item 13, field report
    2026-10-23)."""
    return "transparent" in _error_text(exc)


def _connect_timeout_for(index: int, deadline: float) -> float:
    """Seconds one connect attempt may run (B2): the first
    candidate gets CONNECT_FIRST_TIMEOUT_S, later candidates
    CONNECT_LATER_TIMEOUT_S, and no attempt runs past the shared
    deadline. <= 0 means the budget is spent — do not attempt."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0.0
    base = (CONNECT_FIRST_TIMEOUT_S if index == 0
            else CONNECT_LATER_TIMEOUT_S)
    return min(base, remaining)


def _ambient_packet_text() -> str:
    """The NOSE ambient context packet for session start (v5.44,
    N4), lazily imported (house style): a missing or failing
    ambient module degrades to "" — today's behaviour, byte for
    byte — and never breaks a connect."""
    try:
        from ..brain import ambient  # Phase 3 V1b: moved to core/voice/

        return ambient.ambient_packet() or ""
    except Exception:  # noqa: BLE001 — ambient is a nicety
        return ""


def _live_model_candidates() -> list[str]:
    """Ordered connect attempts: the configured model, then every
    other known current Live id (deduped, configured first)."""
    candidates: list[str] = []
    for model_id in (config.model_name, *KNOWN_LIVE_MODELS):
        model_id = (model_id or "").strip()
        if model_id and model_id not in candidates:
            candidates.append(model_id)
    return candidates
