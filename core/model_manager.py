"""Wave 10 (v5.65) Leaf 3 — PF-13 model & storage manager (STAGED, unwired).

Discover installed models by scanning the tree's REAL model
locations (read from core/voice/local_assets.py, not invented), report
per-model size + total storage use, and remove a model with its
space reclaimed — after which tier resolution falls back cleanly
to the next available tier instead of breaking the app.

Real locations (core/voice/local_assets.py):
- Brain GGUFs: ``data/models/`` — the tier files
  ``Qwen3-1.7B-Q4_K_M.gguf`` (lite),
  ``Qwen3-4B-Instruct-2507-Q4_K_M.gguf`` (balanced),
  ``Qwen3-8B-Instruct-2507-Q4_K_M.gguf`` (quality), plus the VLM
  pair ``Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf`` and
  ``mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf`` in the same dir.
  Any other ``*.gguf`` in ``data/models/`` is reported as a
  ``custom`` brain model (local_assets: a hand-placed file is a
  custom model the user placed by hand).
- Voice: ``data/voices/kokoro/kokoro-v1.0.onnx`` +
  ``voices-v1.0.bin`` (Kokoro), ``data/voices/en_US-amy-medium.onnx``
  (+ ``.json``) (Piper fallback).
- Wake word: ``data/voices/vosk/vosk-model-small-en-us-0.15/``
  (a directory — removed as a whole tree).
- Ears: faster-whisper snapshots live in the HF hub cache or a
  staged ``assets/whisper`` pack, NOT under data/ in a fixed
  layout — so this module does NOT claim a whisper location it
  cannot verify; ``whisper`` models are reported only when the
  caller supplies an explicit snapshot dir (see ``discover_models``
  ``extra_dirs``).

Discovery is a pure scan over a ``data_dir`` (default: the
install's data/ as derived by the coordinator — this module
takes the dir as an argument and never imports config, so it
works with system python and in tests against temp dirs).
``remove_model`` deletes only inside that data dir, refuses
paths that escape it, and returns the reclaimed bytes.

Fallback (never break the app on a missing model):
- ``resolve_tier(availability, preferred=None)`` is pure over an
  injected availability map {tier -> bool}: the preferred tier
  when available, else the next available tier in the order
  quality -> balanced -> lite (local_assets' "already
  downloaded" order: biggest tier present first), else None.
- ``resolve_model(discovered, preferred_file=None)`` builds the
  same answer from real discovery output: {"status": "ok",
  "file", "tier"} when something is available, else
  {"status": "unavailable", "file": None, "tier": None} — an
  honest "unavailable", never a crash, never a phantom path.
  A removed preferred tier therefore resolves to the next
  present tier, and a tree with no brain model at all resolves
  to "unavailable" so the caller can say so plainly.

Stdlib only. Product VERSION is untouched (still 5.64.1); this
module carries no product-version constant of its own.
"""

from __future__ import annotations

import os
import shutil

from .model_tiers import TIER_FILES, TIER_ORDER

# TIER_FILES / TIER_ORDER come from core/model_tiers.py — the one
# canonical tier table (GAPS-22, B-V1/B-V2). They were a hand copy
# of local_assets MODEL_TIERS before; the agreement is by
# construction now, never by hand. TIER_FILES is file -> tier;
# TIER_ORDER is the load path's fallback order (biggest first).
VLM_FILES = {
    "Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf": "vlm",
    "mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf": "vlm-mmproj",
}
KOKORO_FILES = {"kokoro-v1.0.onnx": "voice-kokoro",
                "voices-v1.0.bin": "voice-kokoro"}
PIPER_FILES = {"en_US-amy-medium.onnx": "voice-piper",
               "en_US-amy-medium.onnx.json": "voice-piper"}
WAKE_DIR_NAME = "vosk-model-small-en-us-0.15"


def _dir_size(path: str) -> int:
    """Total bytes under a file or directory tree (0 if missing)."""
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
    return total


def _inside(data_dir: str, path: str) -> bool:
    root = os.path.abspath(data_dir)
    candidate = os.path.abspath(path)
    return candidate == root or candidate.startswith(root + os.sep)


def discover_models(data_dir: str, extra_dirs: dict | None = None
                    ) -> dict:
    """Scan the real model locations under ``data_dir``.

    Returns {"models": [...], "total_bytes": int}. Each model is
    {"id", "kind", "tier" (brain tiers only, else None),
    "path" (absolute), "rel" (relative to data_dir),
    "size_bytes", "is_dir"}. ``extra_dirs`` maps an id prefix to
    an absolute directory to scan as well (the caller-supplied
    whisper snapshot dir); entries there are kind ``whisper``.
    A location that does not exist simply contributes nothing —
    discovery never invents a model.
    """
    models: list[dict] = []

    def _add(model_id: str, kind: str, tier, path: str) -> None:
        if not os.path.exists(path):
            return
        rel = os.path.relpath(path, data_dir).replace(os.sep, "/")
        models.append({
            "id": model_id,
            "kind": kind,
            "tier": tier,
            "path": path,
            "rel": rel,
            "size_bytes": _dir_size(path),
            "is_dir": os.path.isdir(path),
        })

    models_dir = os.path.join(data_dir, "models")
    if os.path.isdir(models_dir):
        for name in sorted(os.listdir(models_dir)):
            path = os.path.join(models_dir, name)
            if name in TIER_FILES:
                _add(name, "brain", TIER_FILES[name], path)
            elif name in VLM_FILES:
                _add(name, VLM_FILES[name], None, path)
            elif name.endswith(".gguf") and os.path.isfile(path):
                _add(name, "brain", None, path)  # custom, hand-placed
    voices_dir = os.path.join(data_dir, "voices")
    kokoro_dir = os.path.join(voices_dir, "kokoro")
    if os.path.isdir(kokoro_dir):
        for name in sorted(os.listdir(kokoro_dir)):
            if name in KOKORO_FILES:
                _add(f"kokoro/{name}", KOKORO_FILES[name], None,
                     os.path.join(kokoro_dir, name))
    if os.path.isdir(voices_dir):
        for name in sorted(os.listdir(voices_dir)):
            if name in PIPER_FILES:
                _add(name, PIPER_FILES[name], None,
                     os.path.join(voices_dir, name))
    wake_dir = os.path.join(voices_dir, "vosk", WAKE_DIR_NAME)
    _add(f"vosk/{WAKE_DIR_NAME}", "wake", None, wake_dir)
    for prefix, extra in (extra_dirs or {}).items():
        if os.path.isdir(extra):
            _add(prefix, "whisper", None, extra)
        elif os.path.isfile(extra):
            _add(prefix, "whisper", None, extra)
    models.sort(key=lambda item: item["id"])
    return {"models": models,
            "total_bytes": sum(m["size_bytes"] for m in models)}


def remove_model(data_dir: str, model_id: str) -> dict:
    """Delete one discovered model; return the reclaimed space.

    Returns {"ok", "model_id", "removed_path", "reclaimed_bytes",
    "error"}. Refuses unknown ids and paths escaping ``data_dir``;
    a missing file for a known id reports ``ok: False`` rather
    than pretending space was reclaimed. Directories (the Vosk
    wake model) are removed as whole trees. This function only
    deletes — tier fallback is ``resolve_model``'s job, and the
    caller re-runs discovery afterwards to see the new state.
    """
    result: dict = {"ok": False, "model_id": model_id,
                    "removed_path": None, "reclaimed_bytes": 0,
                    "error": None}
    discovered = discover_models(data_dir)
    match = next((m for m in discovered["models"]
                  if m["id"] == model_id), None)
    if match is None:
        result["error"] = "model-not-found"
        return result
    path = match["path"]
    if not _inside(data_dir, path):
        result["error"] = "path-escapes-data-dir"
        return result
    reclaimed = match["size_bytes"]
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
    except OSError as exc:
        result["error"] = f"remove-failed: {exc}"
        return result
    result["ok"] = True
    result["removed_path"] = path
    result["reclaimed_bytes"] = reclaimed
    return result


def resolve_tier(availability: dict, preferred: str | None = None
                 ) -> dict:
    """Pure tier fallback over an injected availability map.

    ``availability`` maps tier name -> bool (present or not).
    Preferred tier when available, else the next available tier
    in quality -> balanced -> lite order, else an honest
    {"status": "unavailable", "tier": None} — never a crash.
    """
    if preferred is not None and availability.get(preferred):
        return {"status": "ok", "tier": preferred}
    for tier in TIER_ORDER:
        if availability.get(tier):
            return {"status": "ok", "tier": tier}
    return {"status": "unavailable", "tier": None}


def resolve_model(discovered: dict, preferred_file: str | None = None,
                  min_bytes: int = 0) -> dict:
    """Tier fallback from real discovery output.

    Builds the availability map from the discovered brain models
    (tiered GGUFs only — custom files have no tier and never
    satisfy a tier fallback) and applies ``resolve_tier``. When
    the preferred file is present it wins (even a custom file,
    which resolves with tier None); otherwise the answer names
    the fallback tier's file. With no brain model at all the
    answer is {"status": "unavailable", "file": None,
    "tier": None} — the caller reports that honestly instead of
    loading a phantom path.

    ``min_bytes`` is the readiness floor (GAPS-22, B-V1): a
    brain file of at most this size does not count as present — a
    partial download must never satisfy a tier. The load path
    (``local_assets._existing_tier_file``) passes
    ``MIN_MODEL_BYTES``; callers without a floor pass 0 and get
    the old behaviour. The floor applies to the preferred file
    too: a configured file that is only partly downloaded falls
    through to the next present tier, exactly as the load path
    has always done.
    """
    brains = [m for m in discovered.get("models", [])
              if m["kind"] == "brain"]
    if min_bytes:
        brains = [m for m in brains
                  if int(m.get("size_bytes") or 0) > min_bytes]
    by_file = {m["id"]: m for m in brains}
    if preferred_file is not None and preferred_file in by_file:
        model = by_file[preferred_file]
        return {"status": "ok", "file": model["id"],
                "tier": model["tier"]}
    availability = {m["tier"]: True for m in brains if m["tier"]}
    preferred_tier = (TIER_FILES.get(preferred_file)
                      if preferred_file else None)
    resolved = resolve_tier(availability, preferred_tier)
    if resolved["status"] != "ok":
        return {"status": "unavailable", "file": None, "tier": None}
    tier = resolved["tier"]
    filename = next(name for name, tier_name in TIER_FILES.items()
                    if tier_name == tier)
    return {"status": "ok", "file": filename, "tier": tier}
