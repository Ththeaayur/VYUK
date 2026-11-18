import json
import threading
import time
from pathlib import Path

from ..config import DATA_DIR

# INTENTIONAL (batch 3): cross-module constants for _ASSET_FLOORS.
from .local_assets_kokoro import MIN_KOKORO_MODEL_BYTES, MIN_KOKORO_VOICES_BYTES
from .local_assets_model import MIN_MODEL_BYTES
from .local_assets_piperwake import MIN_PIPER_VOICE_BYTES, MIN_WAKE_MODEL_BYTES


_ASSET_STAGE = {
    "whisper": "ears",
    "kokoro_model": "voice",
    "kokoro_voices": "voice",
    "wake_model": "voice",
    "piper_voice": "voice",
    "piper_config": "voice",
    "brain": "brain",
}


_STAGE_ASSETS = {
    "ears": ("whisper",),
    "voice": ("kokoro_model", "kokoro_voices", "wake_model",
              "piper_voice", "piper_config"),
    "brain": ("brain",),
}


_STAGE_ORDER = ("ears", "voice", "brain")


# A final file only counts as its own total once it clears the same
# sanity floor its *_ready() check uses.
_ASSET_FLOORS = {
    "kokoro_model": MIN_KOKORO_MODEL_BYTES,
    "kokoro_voices": MIN_KOKORO_VOICES_BYTES,
    "wake_model": MIN_WAKE_MODEL_BYTES,
    "piper_voice": MIN_PIPER_VOICE_BYTES,
    "brain": MIN_MODEL_BYTES,
}


_progress_lock = threading.Lock()


_progress_assets: dict[str, dict] = {}


_progress_run_state = "idle"   # idle | downloading | paused | complete | failed


def _progress_begin(asset: str, total: int | None = None, done: int = 0):
    with _progress_lock:
        _progress_assets[asset] = {
            "total": int(total) if total else None,
            "done": max(0, int(done)),
            "state": "downloading",
        }


_KEEP = object()  # sentinel for _progress_update: leave the value alone


def _trim_error(text: str) -> str:
    """Errors recorded for humans and support reports: single line,
    capped at 200 chars."""
    return " ".join(str(text).split())[:200]


def _progress_update(asset: str, done: int | None = None,
                     total: int | None = None, state: str | None = None,
                     error=_KEEP, via: str | None = None):
    """Tracker update. v3.3: a record also carries "error" (the
    asset's last failure, trimmed — cleared when the asset lands) and
    "via" (the host that served or last attempted the asset), which
    pack_progress() surfaces per stage."""
    with _progress_lock:
        rec = _progress_assets.get(asset)
        if rec is None:
            rec = {"total": None, "done": 0, "state": "waiting"}
            _progress_assets[asset] = rec
        if done is not None:
            rec["done"] = max(0, int(done))
        if total:
            rec["total"] = int(total)
        if state is not None:
            rec["state"] = state
            if state == "done":
                rec["error"] = None  # a landed file has no live error
        if error is not _KEEP:
            rec["error"] = _trim_error(error) if error else None
        if via:
            rec["via"] = via


def _progress_set_run_state(state: str):
    global _progress_run_state
    with _progress_lock:
        _progress_run_state = state


# --- Persisted download outcome (v3.3) -----------------------------------
# The tracker above dies with the process, but the most useful facts
# about a failed first run are needed AFTER a restart — by the doctor
# quoting what went wrong, and by launch-time provisioning deciding
# whether an auto-retry is even due. They persist in one tiny JSON
# file in data/ (in-process cache + file kept in step below).
_STATE_FILE_NAME = "download_state.json"


_dl_state_lock = threading.Lock()


_dl_state: dict | None = None  # lazy-loaded cache of the file


# Launch auto-provision waits this long after a failure before it
# may start another attempt on its own; manual starts never wait.
AUTO_RETRY_AFTER_S = 600


def _download_state_path() -> Path:
    return DATA_DIR / _STATE_FILE_NAME


def _download_state() -> dict:
    """{"last_error", "last_failed_at", "last_served_by"} — the
    persisted record of the pack's download history. Never raises:
    a missing or corrupt file reads as an empty history."""
    global _dl_state
    with _dl_state_lock:
        if _dl_state is None:
            state: dict = {"last_error": None, "last_failed_at": None,
                           "last_served_by": None}
            try:
                raw = json.loads(
                    _download_state_path().read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for key in state:
                        if key in raw:
                            state[key] = raw[key]
            except Exception:  # noqa: BLE001 — history is a nicety
                pass
            _dl_state = state
        return dict(_dl_state)


def _save_download_state(patch: dict) -> None:
    """Merges `patch` into the persisted download history. Guarded:
    failing to persist must never break a download."""
    global _dl_state
    with _dl_state_lock:
        if _dl_state is None:
            _dl_state = {"last_error": None, "last_failed_at": None,
                         "last_served_by": None}
        _dl_state.update(patch)
        snapshot = dict(_dl_state)
    try:
        path = _download_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def last_download_error() -> str | None:
    """Public read of the persisted last download error (v5.17) —
    the Settings Brain card shows it where the user actually
    looks, instead of leaving it only in the doctor's output."""
    try:
        return _download_state().get("last_error")
    except Exception:  # noqa: BLE001
        return None


def _record_download_failure(error_text: str) -> None:
    _save_download_state({
        "last_error": _trim_error(error_text),
        "last_failed_at": time.time(),
    })


def _clear_download_failure() -> None:
    _save_download_state({"last_error": None, "last_failed_at": None})


def _record_served_by(host: str) -> None:
    if host:
        _save_download_state({"last_served_by": host})


def last_download_info() -> dict:
    """Public view of the persisted download history (the doctor
    quotes it): {"last_error", "last_failed_at", "last_served_by"},
    each None when nothing is recorded."""
    return _download_state()
