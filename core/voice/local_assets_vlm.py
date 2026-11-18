"""Local vision-language model family of core/voice/local_assets.py (skills split, batch 2)."""
import os
import threading
from collections.abc import Callable
from pathlib import Path
from ..config import config  # Phase 3 V2: config stays flat
from .. import config_spec
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_model).
from .local_assets_model import (
    MODELS_DIR,
    _bundled_child,
    _size,
    effective_model,
)
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_proxy).
from .local_assets_proxy import (
    download_file,
    sha256_of,
)


VLM_MODEL_FILE = "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf"


VLM_MMPROJ_FILE = "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf"


_VLM_REPO = "ggml-org/Qwen2.5-VL-3B-Instruct-GGUF"


VLM_MODEL_URL = (
    f"https://huggingface.co/{_VLM_REPO}/resolve/main/{VLM_MODEL_FILE}")


VLM_MMPROJ_URL = (
    f"https://huggingface.co/{_VLM_REPO}/resolve/main/{VLM_MMPROJ_FILE}")


VLM_MODEL_SHA256 = (
    "d02fe9b69ad8cadbbd228e387667af66612c44bed29ffc8eb1e7caf9ac486c12")


VLM_MMPROJ_SHA256 = (
    "980c9b2f78c04e6cff93d277ada09e768394f112d75db3b4e9dea8a69f9fb904")


MIN_VLM_MODEL_BYTES = 1_500_000_000


MIN_VLM_MMPROJ_BYTES = 500_000_000


def vlm_paths() -> tuple[Path, Path]:
    """The RESOLVED VLM (model, mmproj) paths: per file, the
    bundled install copy wins when it clears the readiness floor
    (the kokoro_paths pattern), else the downloaded copy under
    data/models."""
    model = MODELS_DIR / VLM_MODEL_FILE
    mmproj = MODELS_DIR / VLM_MMPROJ_FILE
    bundled_dir = _bundled_child("models")
    if bundled_dir is not None:
        if _size(bundled_dir / VLM_MODEL_FILE) > MIN_VLM_MODEL_BYTES:
            model = bundled_dir / VLM_MODEL_FILE
        if _size(bundled_dir / VLM_MMPROJ_FILE) > MIN_VLM_MMPROJ_BYTES:
            mmproj = bundled_dir / VLM_MMPROJ_FILE
    return model, mmproj


def download_vlm_paths() -> tuple[Path, Path]:
    """Where VLM (re)downloads land: always data/models — the
    install tree is never written."""
    return (MODELS_DIR / VLM_MODEL_FILE, MODELS_DIR / VLM_MMPROJ_FILE)


def vlm_ready() -> bool:
    """Both halves of the pair are on disk and clear their size
    floors — a model without its mmproj (or vice versa) cannot
    see, so the pair is ready together or not at all."""
    model, mmproj = vlm_paths()
    return (
        _size(model) > MIN_VLM_MODEL_BYTES
        and _size(mmproj) > MIN_VLM_MMPROJ_BYTES
    )


def vlm_explicit_pick() -> bool:
    """The user's explicit VLM pick: the spec value
    (config.local_vlm / env LOCAL_VLM) — truthy spellings only
    (1/true/yes/on), the config._env_bool contract. This is the
    Settings explicit-pick field the tier gate honours below the
    Quality tier."""
    raw = config_spec.env("LOCAL_VLM")
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def vlm_tier_allowed() -> bool:
    """Whether VLM provisioning may run on this machine: the
    effective brain tier is Quality, or the user explicitly
    picked the asset. Below that, NEVER — a Lite/Balanced install
    must not wake up to a surprise ~2.8 GB download."""
    if vlm_explicit_pick():
        return True
    try:
        return effective_model().get("tier") == "quality"
    except Exception:  # noqa: BLE001 — a broken probe is a refusal
        return False


def vlm_status() -> dict:
    """One read-only snapshot for the doctor / Settings:
    {"ready", "allowed", "tier", "explicit_pick", "model_file",
    "mmproj_file"}. Pure reads; never raises."""
    try:
        tier = effective_model().get("tier")
    except Exception:  # noqa: BLE001
        tier = None
    model, mmproj = vlm_paths()
    return {
        "ready": vlm_ready(),
        "allowed": vlm_tier_allowed(),
        "tier": tier,
        "explicit_pick": vlm_explicit_pick(),
        "model_file": model.name,
        "mmproj_file": mmproj.name,
    }


def ensure_vlm_assets(progress_cb: Callable[[str], None],
                      cancel_event: threading.Event | None = None
                      ) -> list[str]:
    """Downloads the VLM pair when missing — THROUGH THE TIER
    GATE. Returns the progress lines it emitted (the same lines
    go to progress_cb live); never raises, house style.

    Below the gate the only line is the refusal, and NOT ONE byte
    is fetched: the gate is checked before any download starts.
    A completed download is SHA-256-verified against the
    publisher hashes above (download_file's own integrity gate
    covers only MODEL_TIERS files); a mismatch deletes the file
    and reports an ERROR line, exactly like a corrupt brain."""
    lines: list[str] = []

    def emit(line: str):
        lines.append(line)
        try:
            progress_cb(line)
        except Exception:  # noqa: BLE001 — a broken callback must not kill setup
            pass

    if vlm_ready():
        emit("Local screen vision (Qwen2.5-VL-3B) ready.")
        return lines
    if not vlm_tier_allowed():
        tier = None
        try:
            tier = effective_model().get("tier")
        except Exception:  # noqa: BLE001
            pass
        emit(
            "Local screen vision (Qwen2.5-VL-3B) is a Quality-tier "
            f"extra — not downloading it on the "
            f"{(tier or 'custom')} tier. Screens are still read "
            "with OCR; switch the brain tier to Quality (or pick "
            "the vision model explicitly in Settings) to fetch it.")
        return lines
    model_dest, mmproj_dest = download_vlm_paths()
    for url, dest, label, expected_sha in (
            (VLM_MODEL_URL, model_dest,
             "vision model (Qwen2.5-VL-3B)", VLM_MODEL_SHA256),
            (VLM_MMPROJ_URL, mmproj_dest,
             "vision projector (mmproj)", VLM_MMPROJ_SHA256)):
        floor = (MIN_VLM_MODEL_BYTES if dest is model_dest
                 else MIN_VLM_MMPROJ_BYTES)
        if _size(dest) > floor:
            continue
        err = download_file(url, dest, emit, label, cancel_event)
        if err == "CANCELLED":
            emit("Download paused — it will resume where it left off.")
            return lines
        if err is not None:
            emit(f"ERROR: {err} (source: {url})")
            return lines
        if sha256_of(dest) != expected_sha:
            try:
                dest.unlink()
            except OSError:
                pass
            emit(
                f"ERROR: download of {label} failed: it failed its "
                "integrity check (SHA-256 mismatch) — the corrupt "
                "file was removed; downloading again is safe "
                f"(source: {url})")
            return lines
    if vlm_ready():
        emit("Local screen vision (Qwen2.5-VL-3B) ready.")
    return lines


def _fetch_vlm_model(emit: Callable[[str], None],
                     cancel_event: threading.Event | None = None) -> None:
    """The pack-run VLM step (called from
    _ensure_local_assets_serialized): fetch the pair ONLY when
    the tier gate allows it; on any other tier this step is
    silent — a Lite/Balanced setup run has nothing to say about
    an extra it will never download. Failures arrive as the
    lines ensure_vlm_assets emits; this wrapper never raises
    and never turns them into pack failures (OCR covers screens
    without the VLM — the wake/Piper note posture)."""
    try:
        if vlm_ready():
            emit("Local screen vision (Qwen2.5-VL-3B) ready.")
        elif vlm_tier_allowed():
            ensure_vlm_assets(emit, cancel_event)
    except Exception:  # noqa: BLE001 — the VLM is an extra, never a blocker
        pass
