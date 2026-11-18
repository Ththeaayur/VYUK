"""Local vision backend adapter (B5, 2026-11-06).

ONE adapter, two model configs, no new inference engine:

- Qwen3-VL-2B (screen understanding) runs through the EXISTING
  llama.cpp VLM machinery — the same Llama + mtmd chat-handler
  shape core/voice/local_client_vlm.py already drives for the
  Qwen2.5-VL-3B pair. Only the pair config is new.
- Granite-Docling-258M (document reading) runs through the
  EXISTING onnxruntime dependency (already used by VAD, SmartTurn
  and the embeddings toolbox) plus transformers' AutoProcessor —
  the model publisher's own reference flow from the ONNX card,
  ported to local paths. No new engine is implemented here.

Weights are NEVER downloaded by this module and NEVER bundled:
a call resolves the pair from disk (bundled install copy, then
data/models, then the VYUK_VISION_MODELS_DIR override) or says
so honestly. The provisioner (ensure_vlm_assets) is unchanged in
this batch — it still provisions the Qwen2.5-VL pair; Qwen3-VL
arrives by hand placement or an explicit pick, and flipping the
provisioner is a named follow-up once Qwen3-VL is field-proven.

Licence re-read (B5, 2026-11-06 — the owed L4 frontmatter
re-read, recorded in core/pilot_licences_senses.py):
- Qwen3-VL: Apache-2.0. Official Qwen/Qwen3-VL-2B-Instruct card
  opened; Ollama's library license blob for qwen3-vl carries the
  full Apache License 2.0 text; Furiosa docs: "released under the
  Apache 2.0 License"; derivative cards: "Base Model License: The
  original Qwen3-VL model is licensed under Apache 2.0 by Alibaba
  Cloud". Commercial use allowed under Apache 2.0 (include the
  license + copyright notice). Note: Qwen's older research licence
  with MAU terms applies to OTHER Qwen models, not Qwen3-VL.
- Granite-Docling-258M: Apache-2.0. onnx-community card
  frontmatter "license: apache-2.0" (base_model
  ibm-granite/granite-docling-258M); IBM announcement: "available
  on Hugging Face through a standard Apache 2.0 license"; IBM
  Granite docs: "released under the Apache 2.0 license, ensuring
  it is freely available for both research and commercial use".

Every call returns a dict with ok/engine/model/reason and
elapsed_s (total) + inference_s (model time) — the timing the
field leg needs to answer whether the model feels fast on his
laptop. A call never fabricates a vision result: weights missing,
a missing handler, a missing optional runtime, or a load failure
all return ok False naming the cause.
"""

from __future__ import annotations

import os
from . import config_spec
import threading
import time
from pathlib import Path

# INTENTIONAL: cross-module import — asset facts live in
# local_assets_model, the same source vlm_paths() reads.
from .voice.local_assets_model import MODELS_DIR, _bundled_child, _size

# ---------------------------------------------------------------------------
# Model configs
# ---------------------------------------------------------------------------

# Qwen3-VL-2B GGUF pair. Repo verified (Jetson Orin guide table +
# unsloth/lmstudio-community listings): ggml-org ships the
# Qwen3-VL-2B-Instruct-GGUF repo. The mmproj filename below is
# verified (yzma USAGE.md example list). The model filename is the
# ggml-org naming convention shared with the Qwen2.5-VL pair — the
# resolution below SCANS for *Qwen3-VL-2B-Instruct*.gguf rather
# than trusting the preferred name, so any quant variant resolves.
QWEN3_VL_REPO = "ggml-org/Qwen3-VL-2B-Instruct-GGUF"
QWEN3_VL_MODEL_FILE = "Qwen3-VL-2B-Instruct-Q4_K_M.gguf"
QWEN3_VL_MMPROJ_FILE = "mmproj-Qwen3-VL-2B-Instruct-Q8_0.gguf"

# Conservative UNMEASURED floors (not publisher-measured pins like
# the Qwen2.5-VL pair's): a real 2B quant file is ~1.1 GB+, a real
# mmproj ~300 MB+, so anything below these is a placeholder or a
# truncation. The load itself is the real check; the floors only
# keep a stub file from counting as ready. Field-measured pins
# replace these once real weights land.
MIN_QWEN3_MODEL_BYTES = 1_000_000_000
MIN_QWEN3_MMPROJ_BYTES = 200_000_000

# Granite-Docling-258M ONNX asset (onnx-community repo, per the
# card's own usage snippet: three sessions + processor + config).
GRANITE_REPO = "onnx-community/granite-docling-258M-ONNX"
GRANITE_DIR_NAME = "granite-docling-258M-ONNX"
GRANITE_ONNX_FILES = (
    "onnx/vision_encoder.onnx",
    "onnx/vision_encoder.onnx_data",
    "onnx/embed_tokens.onnx",
    "onnx/embed_tokens.onnx_data",
    "onnx/decoder_model_merged.onnx",
    "onnx/decoder_model_merged.onnx_data",
)
GRANITE_META_FILES = ("config.json", "preprocessor_config.json")
GRANITE_PROMPT = "Convert this page to docling."

_DEFAULT_DESCRIBE_PROMPT = (
    "Describe this screenshot in plain words for someone who "
    "cannot see it: what app or page it shows, what is on it, "
    "any visible text that matters, and anything that looks like "
    "an error, a prompt waiting for an answer, or a notable "
    "change. Be specific and factual; do not invent details."
)
# Mirrors core/voice/local_client_vlm._VLM_DESCRIBE_PROMPT (kept
# as a copy so this module never imports the frame path — the
# frame path imports this module, not the reverse).

_QWEN_DESCRIBE_MAX_TOKENS = 320
_GRANITE_MAX_NEW_TOKENS = 4096  # the card's own cap


# ---------------------------------------------------------------------------
# Resolution — disk only, never a download
# ---------------------------------------------------------------------------

def _candidate_model_dirs() -> list[Path]:
    """Where vision weights may live, in priority order: the
    explicit override, then the bundled install copy, then
    data/models."""
    dirs: list[Path] = []
    override = config_spec.env("VYUK_VISION_MODELS_DIR").strip()
    if override:
        dirs.append(Path(override))
    bundled = _bundled_child("models")
    if bundled is not None:
        dirs.append(bundled)
    dirs.append(MODELS_DIR)
    return dirs


def _scan_pair(model_glob: str, mmproj_glob: str,
               model_floor: int, mmproj_floor: int,
               preferred_model: str, preferred_mmproj: str,
               ) -> tuple[Path | None, Path | None, list[str]]:
    """Resolve a (model, mmproj) GGUF pair by scan across the
    candidate dirs. Returns (model, mmproj, missing): a path is
    None when no candidate clears its floor. A preferred filename
    wins when present; several non-preferred candidates is an
    ambiguity the caller reports, not a guess."""
    missing: list[str] = []
    model: Path | None = None
    mmproj: Path | None = None
    for directory in _candidate_model_dirs():
        if not directory.is_dir():
            continue
        if model is None:
            preferred = directory / preferred_model
            if _size(preferred) > model_floor:
                model = preferred
            else:
                cands = sorted(
                    p for p in directory.glob(model_glob)
                    if not p.name.startswith("mmproj")
                    and _size(p) > model_floor)
                if len(cands) == 1:
                    model = cands[0]
                elif len(cands) > 1:
                    missing.append(
                        "ambiguous model files: "
                        + ", ".join(p.name for p in cands))
        if mmproj is None:
            preferred_p = directory / preferred_mmproj
            if _size(preferred_p) > mmproj_floor:
                mmproj = preferred_p
            else:
                cands = sorted(
                    p for p in directory.glob(mmproj_glob)
                    if _size(p) > mmproj_floor)
                if len(cands) == 1:
                    mmproj = cands[0]
                elif len(cands) > 1:
                    missing.append(
                        "ambiguous mmproj files: "
                        + ", ".join(p.name for p in cands))
        if model is not None and mmproj is not None:
            break
    if model is None and not any("model files" in m for m in missing):
        missing.append("model file (*Qwen3-VL-2B-Instruct*.gguf)")
    if mmproj is None and not any("mmproj files" in m for m in missing):
        missing.append("mmproj file (mmproj-Qwen3-VL-2B-Instruct*.gguf)")
    return model, mmproj, missing


def qwen3_vlm_paths() -> tuple[Path | None, Path | None, list[str]]:
    """The resolved Qwen3-VL-2B pair, or (None, None, missing)."""
    return _scan_pair(
        "*Qwen3-VL-2B-Instruct*.gguf",
        "mmproj-Qwen3-VL-2B-Instruct*.gguf",
        MIN_QWEN3_MODEL_BYTES, MIN_QWEN3_MMPROJ_BYTES,
        QWEN3_VL_MODEL_FILE, QWEN3_VL_MMPROJ_FILE)


def qwen3_vlm_ready() -> bool:
    """True when both halves of the Qwen3-VL-2B pair are on disk
    and clear their floors."""
    model, mmproj, _ = qwen3_vlm_paths()
    return model is not None and mmproj is not None


def granite_dir() -> tuple[Path | None, list[str]]:
    """The resolved Granite-Docling ONNX snapshot dir, or
    (None, missing-files). The snapshot mirrors the repo layout:
    the six onnx/ files plus config.json and
    preprocessor_config.json at its root."""
    for directory in _candidate_model_dirs():
        candidate = directory / GRANITE_DIR_NAME
        if not candidate.is_dir():
            continue
        missing = [f for f in GRANITE_ONNX_FILES + GRANITE_META_FILES
                   if not (candidate / f).is_file()]
        if not missing:
            return candidate, []
        return candidate, missing
    return None, list(GRANITE_ONNX_FILES + GRANITE_META_FILES)


def granite_ready() -> bool:
    """True when the Granite-Docling ONNX snapshot is complete."""
    directory, missing = granite_dir()
    return directory is not None and not missing


def vision_status() -> dict:
    """One read-only snapshot for the doctor / Settings. Pure
    reads; never raises."""
    qwen_model, qwen_mmproj, qwen_missing = qwen3_vlm_paths()
    granite_path, granite_missing = granite_dir()
    return {
        "qwen3-vl-2b": {
            "ready": qwen_model is not None and qwen_mmproj is not None,
            "repo": QWEN3_VL_REPO,
            "model_file": qwen_model.name if qwen_model else None,
            "mmproj_file": qwen_mmproj.name if qwen_mmproj else None,
            "missing": qwen_missing,
        },
        "granite-docling-258m": {
            "ready": granite_path is not None and not granite_missing,
            "repo": GRANITE_REPO,
            "dir": str(granite_path) if granite_path else None,
            "missing": granite_missing,
        },
    }


# ---------------------------------------------------------------------------
# Qwen3-VL leg — the existing llama.cpp VLM machinery
# ---------------------------------------------------------------------------

_llama_cache: dict[str, object] = {}
_llama_failed: set[str] = set()
_llama_lock = threading.Lock()


def _qwen3_handler_class():
    """The Qwen3-VL mtmd chat handler of the installed
    llama-cpp-python build, or None when the build lacks it
    (older wheel, non-mtmd build). Confirmed to exist in
    vision-capable wheels (Qwen3VLChatHandler)."""
    try:
        from llama_cpp.llama_chat_format import Qwen3VLChatHandler

        return Qwen3VLChatHandler
    except Exception:  # noqa: BLE001 — any import surprise = unavailable
        return None


def qwen3_handler_available() -> bool:
    """True when the installed llama_cpp build exposes the
    Qwen3-VL chat handler."""
    return _qwen3_handler_class() is not None


def _load_qwen3_llama():
    """Loads (and caches) the Qwen3-VL Llama with its mtmd chat
    handler — the _ensure_vlm_llama pattern (GPU first, one CPU
    retry, then a latched failure). Raises on failure; the
    caller converts it into the honest load-failed record."""
    key = "qwen3-vl-2b"
    if key in _llama_cache:
        return _llama_cache[key]
    if key in _llama_failed:
        raise RuntimeError("Qwen3-VL load already failed this run")
    with _llama_lock:
        if key in _llama_cache:
            return _llama_cache[key]
        model_path, mmproj_path, _ = qwen3_vlm_paths()
        handler_cls = _qwen3_handler_class()
        if handler_cls is None:
            raise RuntimeError(
                "no Qwen3VLChatHandler in the installed llama_cpp "
                "build")
        from llama_cpp import Llama

        last_exc: Exception | None = None
        for n_gpu_layers in (-1, 0):
            try:
                handler = handler_cls(
                    clip_model_path=str(mmproj_path), verbose=False)
                llama = Llama(
                    model_path=str(model_path),
                    chat_handler=handler,
                    n_ctx=4096,
                    n_gpu_layers=n_gpu_layers,
                    verbose=False,
                )
                _llama_cache[key] = llama
                return llama
            except Exception as exc:  # noqa: BLE001 — one CPU retry
                last_exc = exc
        _llama_failed.add(key)
        raise RuntimeError(
            f"Qwen3-VL load failed: {last_exc}") from last_exc


def describe(image_bytes: bytes, prompt: str = "",
             model: str = "qwen3-vl-2b",
             max_tokens: int = _QWEN_DESCRIBE_MAX_TOKENS) -> dict:
    """Describe an image with the local Qwen3-VL model.

    Returns ok True with {text, elapsed_s, inference_s} on a
    model-produced description, else ok False with a reason
    (unknown-model / model-not-wired / empty-image /
    weights-missing / handler-unavailable / load-failed /
    empty-reply) naming what is missing. Never a fabricated
    description."""
    t0 = time.monotonic()
    base = {"engine": "qwen3-vl", "model": model}

    def _fail(reason: str, detail: str) -> dict:
        return {**base, "ok": False, "reason": reason, "detail": detail,
                "elapsed_s": round(time.monotonic() - t0, 3)}

    if model not in ("qwen3-vl-2b", "qwen3-vl-4b"):
        return _fail("unknown-model",
                     f"No Qwen3-VL model named {model!r} exists.")
    if model != "qwen3-vl-2b":
        return _fail(
            "model-not-wired",
            "Only the Qwen3-VL-2B pair is wired in this batch; "
            "the 4B pair has no backend yet.")
    if not image_bytes:
        return _fail("empty-image", "No image bytes were supplied.")
    model_path, mmproj_path, missing = qwen3_vlm_paths()
    if model_path is None or mmproj_path is None:
        return _fail(
            "weights-missing",
            "The Qwen3-VL-2B GGUF pair is not on disk "
            f"({'; '.join(missing)}). Place the pair from "
            f"{QWEN3_VL_REPO} under data/models (or set "
            "VYUK_VISION_MODELS_DIR); nothing is downloaded "
            "silently.")
    if not qwen3_handler_available():
        return _fail(
            "handler-unavailable",
            "The installed llama_cpp build has no "
            "Qwen3VLChatHandler — the vision wheel is missing or "
            "too old.")
    try:
        llama = _load_qwen3_llama()
    except Exception as exc:  # noqa: BLE001 — load failure is a record
        return _fail("load-failed", str(exc))
    import base64

    data_uri = ("data:image/jpeg;base64,"
                + base64.b64encode(image_bytes).decode("ascii"))
    text_prompt = (prompt or "").strip() or _DEFAULT_DESCRIBE_PROMPT
    t_inf = time.monotonic()
    try:
        reply = llama.create_chat_completion(
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": data_uri}},
                    {"type": "text", "text": text_prompt},
                ],
            }],
            max_tokens=max_tokens,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001 — inference failure is a record
        return _fail("load-failed", f"inference failed: {exc}")
    inference_s = round(time.monotonic() - t_inf, 3)
    choices = (reply or {}).get("choices") or []
    message = (choices[0].get("message") if choices else {}) or {}
    text = (message.get("content") or "").strip()
    if not text:
        return {**base, "ok": False, "reason": "empty-reply",
                "detail": "The model returned no description.",
                "elapsed_s": round(time.monotonic() - t0, 3),
                "inference_s": inference_s}
    return {**base, "ok": True, "text": text,
            "elapsed_s": round(time.monotonic() - t0, 3),
            "inference_s": inference_s,
            "handler": "Qwen3VLChatHandler"}


# ---------------------------------------------------------------------------
# Granite-Docling leg — the ONNX card's own reference flow
# ---------------------------------------------------------------------------

_granite_cache: dict | None = None
_granite_failed = False
_granite_lock = threading.Lock()


def _load_granite() -> dict:
    """Loads (and caches) the Granite-Docling ONNX runtime: the
    processor + config via transformers, and the three
    onnxruntime sessions from the card's usage snippet. Raises on
    failure; the caller converts it into the honest record."""
    global _granite_cache, _granite_failed
    if _granite_cache is not None:
        return _granite_cache
    if _granite_failed:
        raise RuntimeError("Granite-Docling load already failed this run")
    with _granite_lock:
        if _granite_cache is not None:
            return _granite_cache
        directory, missing = granite_dir()
        if directory is None or missing:
            raise RuntimeError(
                "Granite-Docling weights missing: "
                + "; ".join(missing))
        try:
            from transformers import AutoConfig, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "transformers is not installed in this build, so "
                "the Granite-Docling processor cannot run") from exc
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError(
                "onnxruntime is not installed in this build, so "
                "the Granite-Docling sessions cannot run") from exc
        try:
            config = AutoConfig.from_pretrained(str(directory))
            processor = AutoProcessor.from_pretrained(str(directory))
            onnx_dir = directory / "onnx"
            sessions = {
                "vision": ort.InferenceSession(
                    str(onnx_dir / "vision_encoder.onnx")),
                "embed": ort.InferenceSession(
                    str(onnx_dir / "embed_tokens.onnx")),
                "decoder": ort.InferenceSession(
                    str(onnx_dir / "decoder_model_merged.onnx")),
            }
        except Exception as exc:  # noqa: BLE001 — load failure is a record
            _granite_failed = True
            raise RuntimeError(
                f"Granite-Docling load failed: {exc}") from exc
        _granite_cache = {
            "config": config, "processor": processor,
            "sessions": sessions, "dir": directory,
        }
        return _granite_cache


def read_page(image_bytes: bytes, bbox: tuple | None = None,
              max_new_tokens: int = _GRANITE_MAX_NEW_TOKENS) -> dict:
    """Read a page (or one bbox region) to structured DocTags
    text with the local Granite-Docling-258M model — the ONNX
    card's three-session generation loop, ported to local paths.

    Returns ok True with {text, doctags, mode, elapsed_s,
    inference_s} on a model-produced read, else ok False with a
    reason (empty-image / bad-bbox / weights-missing /
    transformers-absent / onnxruntime-absent / load-failed /
    empty-reply) naming what is missing. Never a fabricated
    transcription."""
    t0 = time.monotonic()
    base = {"engine": "granite-docling-258m",
            "mode": "region" if bbox is not None else "full-page",
            "bbox": list(bbox) if bbox is not None else None}

    def _fail(reason: str, detail: str) -> dict:
        return {**base, "ok": False, "reason": reason, "detail": detail,
                "elapsed_s": round(time.monotonic() - t0, 3)}

    if not image_bytes:
        return _fail("empty-image", "No image bytes were supplied.")
    directory, missing = granite_dir()
    if directory is None or missing:
        return _fail(
            "weights-missing",
            "The Granite-Docling ONNX snapshot is not on disk "
            f"({'; '.join(missing)}). Place the "
            f"{GRANITE_DIR_NAME} snapshot from {GRANITE_REPO} "
            "under data/models (or set VYUK_VISION_MODELS_DIR); "
            "nothing is downloaded silently.")
    try:
        from PIL import Image
    except ImportError as exc:
        return _fail("load-failed",
                     f"PIL is not installed in this build: {exc}")
    import io

    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception as exc:  # noqa: BLE001 — undecodable bytes
        return _fail("bad-image", f"Image bytes could not be decoded: {exc}")
    if bbox is not None:
        try:
            x, y, w, h = (int(v) for v in bbox)
            if w <= 0 or h <= 0:
                raise ValueError("non-positive region size")
            image = image.crop((x, y, x + w, y + h))
        except Exception as exc:  # noqa: BLE001 — bad bbox is a record
            return _fail("bad-bbox", f"bbox {bbox!r} is unusable: {exc}")
    try:
        runtime = _load_granite()
    except Exception as exc:  # noqa: BLE001 — load failure is a record
        detail = str(exc)
        if "transformers is not installed" in detail:
            return _fail("transformers-absent", detail)
        if "onnxruntime is not installed" in detail:
            return _fail("onnxruntime-absent", detail)
        return _fail("load-failed", detail)
    import numpy as np

    config = runtime["config"]
    processor = runtime["processor"]
    sessions = runtime["sessions"]
    text_config = config.text_config
    num_key_value_heads = text_config.num_key_value_heads
    head_dim = text_config.head_dim
    num_hidden_layers = text_config.num_hidden_layers
    eos_token_id = text_config.eos_token_id
    image_token_id = config.image_token_id
    messages = [{
        "role": "user",
        "content": [
            {"type": "image"},
            {"type": "text", "text": GRANITE_PROMPT},
        ],
    }]
    t_inf = time.monotonic()
    try:
        prompt = processor.apply_chat_template(
            messages, add_generation_prompt=True)
        inputs = processor(text=prompt, images=[image],
                           return_tensors="np")
        batch_size = inputs["input_ids"].shape[0]
        past_key_values = {
            f"past_key_values.{layer}.{kv}": np.zeros(
                [batch_size, num_key_value_heads, 0, head_dim],
                dtype=np.float32)
            for layer in range(num_hidden_layers)
            for kv in ("key", "value")
        }
        image_features = None
        input_ids = inputs["input_ids"]
        attention_mask = inputs["attention_mask"]
        generated_tokens = np.array([[]], dtype=np.int64)
        for _ in range(max_new_tokens):
            inputs_embeds = sessions["embed"].run(
                None, {"input_ids": input_ids})[0]
            if image_features is None:
                image_features = sessions["vision"].run(None, dict(
                    pixel_values=inputs["pixel_values"],
                    pixel_attention_mask=inputs[
                        "pixel_attention_mask"].astype(np.bool_),
                ))[0]
                inputs_embeds[
                    inputs["input_ids"] == image_token_id
                ] = image_features.reshape(
                    -1, image_features.shape[-1])
            logits, *present_key_values = sessions["decoder"].run(
                None, dict(
                    inputs_embeds=inputs_embeds,
                    attention_mask=attention_mask,
                    **past_key_values,
                ))
            input_ids = logits[:, -1].argmax(-1, keepdims=True)
            attention_mask = np.concatenate(
                [attention_mask, np.ones((batch_size, 1), dtype=np.int64)],
                axis=-1)
            for j, key in enumerate(past_key_values):
                past_key_values[key] = present_key_values[j]
            generated_tokens = np.concatenate(
                [generated_tokens, input_ids], axis=-1)
            if (input_ids == eos_token_id).all():
                break
        text = processor.batch_decode(
            generated_tokens, skip_special_tokens=False)[0]
    except Exception as exc:  # noqa: BLE001 — inference failure is a record
        return _fail("load-failed", f"inference failed: {exc}")
    inference_s = round(time.monotonic() - t_inf, 3)
    text = (text or "").strip()
    if not text:
        return {**base, "ok": False, "reason": "empty-reply",
                "detail": "The model returned no text.",
                "elapsed_s": round(time.monotonic() - t0, 3),
                "inference_s": inference_s}
    return {**base, "ok": True, "text": text, "doctags": text,
            "elapsed_s": round(time.monotonic() - t0, 3),
            "inference_s": inference_s}
