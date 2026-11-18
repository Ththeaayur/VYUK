import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..config import DATA_DIR

# INTENTIONAL (batch 3): live-read of the rebound _progress_run_state
# through the owning module object (B37 pattern); all other M1 names
# below are stable (functions, constants, in-place-mutated dict/lock).
from . import local_assets_progress as _prog
from .local_assets_progress import (
    AUTO_RETRY_AFTER_S,  # INTENTIONAL (batch 3, prover-caught pre-test): auto_start_allowed reads the cadence constant.
    _ASSET_FLOORS,
    _ASSET_STAGE,
    _STAGE_ASSETS,
    _STAGE_ORDER,
    _download_state,
    _progress_assets,
    _progress_lock,
)
# INTENTIONAL (batch 3): cross-module imports (diagnose transport + pack paths).
from .local_assets_kokoro import KOKORO_DIR, kokoro_paths, voice_ready
from .local_assets_model import MODELS_DIR, _size, bundled_assets_dir, model_path, brain_ready
from .local_assets_piperwake import (
    VOICES_DIR,
    WAKE_DIR,
    download_piper_paths,
    download_wake_zip_path,
    wake_model_path,
)
from .local_assets_proxy import (
    _build_opener,
    _merged_proxies,
    _open_response,
    _usable_proxy,
)
from .local_assets_whisper import (
    _hub_repo_dir_name,
    _whisper_hub_dir,
    effective_whisper_model_name,
    whisper_ready,
    whisper_repo_for,
)


def _probe_host(opener, url: str) -> str:
    """"reachable" / "unreachable (<short error>)" for one host,
    probed through the download path's own opener with a 6-second
    timeout. Any HTTP answer at all — even a 404 — proves the host
    was reached, so an HTTPError reads as reachable; only transport
    failures (DNS, refused, timed out) count as unreachable."""
    if opener is None:
        return "unreachable (no opener available)"
    req = urllib.request.Request(
        url, headers={"User-Agent": "VYUK/local-assets"},
        method="HEAD")
    try:
        with _open_response(req, opener, 6) as resp:
            _ = getattr(resp, "status", None)
        return "reachable"
    except urllib.error.HTTPError:
        return "reachable"
    except Exception as e:  # noqa: BLE001 — a probe must not raise
        reason = getattr(e, "reason", e)
        return f"unreachable ({str(reason)[:80]})"


def _diagnose_proxy_line() -> str:
    """The proxy half of diagnose_downloads(): "scheme://host:port"
    of the proxy downloads will use — the downloader's own
    detection (_merged_proxies + _usable_proxy), https preferred —
    credentials never shown; "none detected" when there is none."""
    try:
        clean: dict[str, str] = {}
        for scheme, value in _merged_proxies().items():
            usable = _usable_proxy(value)
            if usable:
                clean[scheme] = usable
        for scheme in ("https", "http"):
            value = clean.get(scheme)
            if not value:
                continue
            parts = urllib.parse.urlsplit(value)
            host = parts.hostname or ""
            if not host:
                continue
            shown = f"{scheme}://{host}"
            if parts.port:
                shown += f":{parts.port}"
            return f"{shown} (credentials never shown)"
    except Exception:  # noqa: BLE001 — diagnosis must not fail
        pass
    return "none detected"


def _diagnose_disk_line() -> str:
    """The disk half of diagnose_downloads(): free space on the
    volume downloads land in (the data dir's volume), one decimal,
    or an honest "unknown"."""
    try:
        target = DATA_DIR if DATA_DIR.exists() else DATA_DIR.parent
        free = shutil.disk_usage(str(target)).free
        return f"{free / (1024 ** 3):.1f} GB"
    except Exception:  # noqa: BLE001 — diagnosis must not fail
        return "unknown"


def diagnose_downloads() -> list[str]:
    """Plain-language download diagnosis (v5.17): can this machine
    reach the hosts the offline pack and the cloud brain need, is a
    system proxy in play, is there disk room where downloads land,
    and what did the last failed download say? Short lines a
    non-technical user can read verbatim — or read TO support.
    Never raises; at most 8 lines."""
    lines: list[str] = []
    try:
        opener, _desc = _build_opener()
    except Exception:  # noqa: BLE001 — diagnosis must not fail
        opener = None
    probes = (
        ("Hugging Face", "https://huggingface.co"),
        ("HF Mirror", "https://hf-mirror.com"),
        ("GitHub", "https://github.com"),
        ("Google AI (Gemini)", "https://generativelanguage.googleapis.com"),
    )
    for name, url in probes:
        lines.append(f"{name}: {_probe_host(opener, url)}")
    lines.append(f"System proxy: {_diagnose_proxy_line()}")
    lines.append(
        f"Free disk where downloads land: {_diagnose_disk_line()}")
    try:
        last_error = _download_state().get("last_error")
    except Exception:  # noqa: BLE001 — history is a nicety
        last_error = None
    if last_error:
        lines.append(f"Last download error: {last_error}")
    return lines[:8]


def auto_start_allowed(now: float | None = None) -> bool:
    """Whether LAUNCH-time auto-provision may start a pack download.

    False while a run is downloading or paused (a pause is the
    user's — respected), and False when the last failed run ended
    less than AUTO_RETRY_AFTER_S ago: a network that just refused
    three gigabytes gets breathing room, not a hammering at every
    app start. True when nothing has failed yet, the last run simply
    ended, or the failure is older than the cadence. Manual starts
    (Settings button, banner Resume) never consult this — the user's
    click is always honoured. `now` (epoch seconds) is injectable
    for tests."""
    with _progress_lock:
        state = _prog._progress_run_state  # INTENTIONAL (batch 3): live-read through the M1 module object — _progress_run_state is rebound by _progress_set_run_state, so a snapshot import goes stale (B37 pattern).
    if state in ("downloading", "paused"):
        return False
    failed_at = _download_state().get("last_failed_at")
    if failed_at:
        current = time.time() if now is None else float(now)
        try:
            if current - float(failed_at) < AUTO_RETRY_AFTER_S:
                return False
        except (TypeError, ValueError):
            pass
    return True


def _whisper_cache_bytes() -> int:
    """Bytes of the configured Whisper size's cache dir — the ears
    live outside data/, in faster-whisper's Hugging Face cache, and
    they are measured, never estimated."""
    repo_dir = _whisper_hub_dir() / _hub_repo_dir_name(
        whisper_repo_for(effective_whisper_model_name()))
    return _tree_bytes(repo_dir)[0]


def _file_or_part_size(path: Path) -> int:
    """Final-file size, else the ".part" resume size, else 0. While a
    download is in flight the .part IS the on-disk truth (its bytes
    are flushed chunk by chunk), so progress can be read off disk."""
    size = _size(path)
    if size > 0:
        return size
    return _size(path.with_suffix(path.suffix + ".part"))


def pack_progress() -> dict:
    """One truthful snapshot of the offline pack's setup state.

    Returns {"stages": [...], "current_stage", "overall_pct",
    "overall_done_bytes", "overall_total_bytes", "ready",
    "complete", "state"}. Stages come in download order (ears, voice,
    brain); each is {"stage", "state" (done | downloading | paused |
    partial | waiting), "done_bytes", "total_bytes", "pct", "ready"}.
    v3.3 adds, per stage, "error" (its last failure, trimmed, or
    None) and "via" (the host that served / last attempted it), and
    at top level "last_error" / "last_failed_at" / "last_served_by"
    from the persisted download history — all additive; existing
    consumers read the keys they always read.
    A total is None when it is not yet knowable — the ears in
    particular declare no total (faster-whisper fetches its own
    weights), so their stage reports bytes landed and a percentage
    only once they are ready. overall_pct is bytes-weighted over the
    assets whose totals ARE known and is capped at 99 until the pack
    is actually complete: this function never invents a number.
    Read-only and cheap: a handful of stat calls plus the whisper
    cache walk.
    """
    ready = {
        "ears": whisper_ready(),
        "voice": voice_ready(),
        "brain": brain_ready(),
    }
    with _progress_lock:
        assets = {key: dict(rec) for key, rec in _progress_assets.items()}
        run_state = _prog._progress_run_state  # INTENTIONAL (batch 3): live-read through the M1 module object — rebound scalar, see above (B37 pattern).

    kokoro_model, kokoro_voices = kokoro_paths()
    brain_path = model_path()
    # The wake model's on-disk truth is two-part: the zip (or its
    # .part) while it downloads, the extracted tree once unpacked —
    # whichever is larger is where the asset actually stands.
    wake_tree_bytes = _tree_bytes(wake_model_path())[0]
    piper_model, piper_config = download_piper_paths()
    disk_done = {
        "whisper": _whisper_cache_bytes(),
        "kokoro_model": _file_or_part_size(kokoro_model),
        "kokoro_voices": _file_or_part_size(kokoro_voices),
        "wake_model": max(_file_or_part_size(download_wake_zip_path()),
                          wake_tree_bytes),
        "piper_voice": _file_or_part_size(piper_model),
        "piper_config": _file_or_part_size(piper_config),
        "brain": _file_or_part_size(brain_path),
    }
    final_size = {
        "kokoro_model": _size(kokoro_model),
        "kokoro_voices": _size(kokoro_voices),
        "wake_model": wake_tree_bytes,
        "piper_voice": _size(piper_model),
        "piper_config": _size(piper_config),
        "brain": _size(brain_path),
    }

    # Per-asset truth: done = the larger of the tracker record and
    # the disk (they agree in flight; the disk wins after a restart);
    # total = the tracker's declared Content-Length, else a finished
    # file's own size once it clears its readiness floor.
    per_asset: dict[str, tuple[int, int | None, str | None]] = {}
    for key in _ASSET_STAGE:
        rec = assets.get(key) or {}
        done = max(int(rec.get("done") or 0), disk_done[key])
        total = rec.get("total")
        if not total and final_size.get(key, 0) > _ASSET_FLOORS.get(key, 0):
            total = final_size[key]
        per_asset[key] = (done, int(total) if total else None, rec.get("state"))

    stages = []
    overall_done = 0
    overall_total = 0
    for stage in _STAGE_ORDER:
        keys = _STAGE_ASSETS[stage]
        done = sum(per_asset[key][0] for key in keys)
        if stage == "ears":
            # No declared total exists for the ears: bytes only,
            # until they are ready — then done IS the total.
            total = done if ready["ears"] else None
        elif all(per_asset[key][1] for key in keys):
            total = sum(per_asset[key][1] for key in keys)
        else:
            total = None
        pct = None
        if ready[stage]:
            pct = 100.0
        elif total:
            pct = min(99.0, 100.0 * done / total)
        if ready[stage]:
            state = "done"
        elif run_state == "downloading" and any(
            per_asset[key][2] == "downloading" for key in keys
        ):
            state = "downloading"
        elif run_state == "paused" and done > 0:
            state = "paused"
        elif done > 0:
            state = "partial"
        else:
            state = "waiting"
        stage_error = None
        stage_via = None
        for key in keys:
            rec = assets.get(key) or {}
            if stage_error is None and rec.get("error"):
                stage_error = rec["error"]
            if rec.get("via"):
                stage_via = rec["via"]
        if ready[stage]:
            stage_error = None  # landed since — the failure is history
        stages.append({
            "stage": stage,
            "state": state,
            "done_bytes": done,
            "total_bytes": total,
            "pct": pct,
            "ready": ready[stage],
            "error": stage_error,
            "via": stage_via,
        })
        # The overall weighting counts every asset whose total is
        # known (a finished file's size is a total), even when its
        # stage-mate's total is still unknown.
        for key in keys:
            asset_done, asset_total = per_asset[key][0], per_asset[key][1]
            if asset_total:
                overall_done += min(asset_done, asset_total)
                overall_total += asset_total

    complete = all(ready.values())
    overall_pct = None
    if overall_total > 0:
        overall_pct = 100.0 * overall_done / overall_total
        if complete:
            overall_pct = 100.0
        elif overall_pct >= 100.0:
            overall_pct = 99.0
    current_stage = next((s for s in _STAGE_ORDER if not ready[s]), None)
    if complete:
        state = "complete"
    elif run_state in ("downloading", "paused", "failed", "error"):
        state = run_state
    else:
        state = "idle"
    history = _download_state()
    return {
        "stages": stages,
        "current_stage": current_stage,
        "overall_pct": overall_pct,
        "overall_done_bytes": overall_done,
        "overall_total_bytes": overall_total if overall_total > 0 else None,
        "ready": ready,
        "complete": complete,
        "state": state,
        "last_error": history.get("last_error"),
        "last_failed_at": history.get("last_failed_at"),
        "last_served_by": history.get("last_served_by"),
    }


# --- Storage accounting & removal ---------------------------------------
def format_bytes(num_bytes: int) -> str:
    """Human size for UI/doctor strings: "3.3 GB", "812 MB", "0 B"
    (one decimal, trailing ".0" dropped)."""
    def one(value: float) -> str:
        text = f"{value:.1f}"
        return text[:-2] if text.endswith(".0") else text

    if num_bytes >= 1_000_000_000:
        return f"{one(num_bytes / 1_000_000_000)} GB"
    if num_bytes >= 1_000_000:
        return f"{one(num_bytes / 1_000_000)} MB"
    if num_bytes >= 1000:
        return f"{one(num_bytes / 1000)} KB"
    return f"{num_bytes} B"


def _tree_bytes(root: Path) -> tuple[int, int]:
    """(total bytes, *.part bytes) under `root`; missing dir = (0, 0)."""
    total = 0
    partial = 0
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                size = _size(path)
                total += size
                if path.name.endswith(".part"):
                    partial += size
    return total, partial


def storage_report() -> dict:
    """Disk usage of the offline pack: recursive sizes of data/models
    and data/voices (the voices figure includes any Piper files the
    user dropped there — they share the folder), with *.part resume
    state accounted separately as "partial_bytes".

    v4.0 adds the bundled install copy as its own figures (additive
    keys — existing consumers read the ones they always read):
    "bundled" says whether this install carries a bundled pack at
    all, "bundled_bytes" is its size on disk. The data/ figures keep
    their meaning: only the downloaded/repair copy is counted there,
    and only it is removable (see remove_offline_pack)."""
    models_total, models_partial = _tree_bytes(MODELS_DIR)
    voices_total, voices_partial = _tree_bytes(VOICES_DIR)
    bundled_root = bundled_assets_dir()
    bundled_bytes = 0
    if bundled_root is not None:
        bundled_models, _bp1 = _tree_bytes(bundled_root / "models")
        bundled_voices, _bp2 = _tree_bytes(bundled_root / "voices")
        bundled_whisper, _bp3 = _tree_bytes(bundled_root / "whisper")
        bundled_bytes = bundled_models + bundled_voices + bundled_whisper
    return {
        "models_bytes": models_total,
        "voices_bytes": voices_total,
        "total_bytes": models_total + voices_total,
        "partial_bytes": models_partial + voices_partial,
        "bundled": bundled_root is not None,
        "bundled_bytes": bundled_bytes,
    }


def remove_offline_pack() -> str:
    """Deletes the downloaded offline pack and returns a human line.

    Scope is deliberately narrow: every file inside data/models/,
    inside data/voices/kokoro/ and inside data/voices/vosk/ (final
    files, the wake model zip AND ".part" resume state).
    Nothing else is touched — never the database, plans or routines,
    never anything outside those two directories, and never files the
    user dropped directly in data/voices/ (Piper voices live there and
    are the user's, not the pack's).

    v4.0: the BUNDLED pack (inside the install) is never deleted —
    it is part of the app, not the user's downloads. When one is
    present the report says so, so "removing the pack" can never be
    mistaken for removing the brain the install shipped with.
    """
    freed = 0
    for root in (MODELS_DIR, KOKORO_DIR, WAKE_DIR):
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*"), reverse=True):
            try:
                if path.is_file() or path.is_symlink():
                    size = _size(path)
                    path.unlink()
                    freed += size
                elif path.is_dir():
                    path.rmdir()  # only succeeds once empty — guarded
            except OSError:
                pass
    bundled = bundled_assets_dir() is not None
    if freed <= 0:
        if bundled:
            return ("Nothing to remove — no downloaded pack is on this PC. "
                    "The copy bundled with the install is part of the app "
                    "and stays.")
        return "Nothing to remove — the offline pack isn't on this PC."
    message = f"Removed the offline pack — freed {format_bytes(freed)}."
    if bundled:
        message += (" The copy bundled with the install is part of the "
                    "app and stays.")
    return message
