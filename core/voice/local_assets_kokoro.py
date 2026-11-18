"""Kokoro voice bundle family of core/voice/local_assets.py (skills split, batch 2)."""
from pathlib import Path
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_model).
from .local_assets_model import (
    _bundled_child,
    _size,
)
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_piperwake).
from .local_assets_piperwake import (
    VOICES_DIR,
)


KOKORO_DIR = VOICES_DIR / "kokoro"


_KOKORO_RELEASE = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)


KOKORO_MODEL_URL = f"{_KOKORO_RELEASE}/kokoro-v1.0.onnx"


KOKORO_VOICES_URL = f"{_KOKORO_RELEASE}/voices-v1.0.bin"


MIN_KOKORO_MODEL_BYTES = 1_000_000       # the real ONNX is ~310 MB


MIN_KOKORO_VOICES_BYTES = 100_000        # the real voices bin is ~27 MB


def kokoro_paths() -> tuple[Path, Path]:
    """The RESOLVED Kokoro (model, voices) paths: per file, the
    bundled install copy wins when it clears the readiness floor
    (the same floor kokoro_ready() applies), else the downloaded
    copy under data/voices/kokoro. A half-bundled install (one file
    short) resolves the short file to data/, where the download
    lands — the pair can legitimately live in two places."""
    model = KOKORO_DIR / "kokoro-v1.0.onnx"
    voices = KOKORO_DIR / "voices-v1.0.bin"
    bundled_dir = _bundled_child("voices", "kokoro")
    if bundled_dir is not None:
        if _size(bundled_dir / "kokoro-v1.0.onnx") > MIN_KOKORO_MODEL_BYTES:
            model = bundled_dir / "kokoro-v1.0.onnx"
        if _size(bundled_dir / "voices-v1.0.bin") > MIN_KOKORO_VOICES_BYTES:
            voices = bundled_dir / "voices-v1.0.bin"
    return (model, voices)


def download_kokoro_paths() -> tuple[Path, Path]:
    """Where Kokoro (re)downloads land: always data/voices/kokoro
    (see download_model_path — the install tree is never written)."""
    return (KOKORO_DIR / "kokoro-v1.0.onnx", KOKORO_DIR / "voices-v1.0.bin")


def kokoro_ready() -> bool:
    model, voices = kokoro_paths()
    return (
        _size(model) > MIN_KOKORO_MODEL_BYTES
        and _size(voices) > MIN_KOKORO_VOICES_BYTES
    )


def voice_ready() -> bool:
    """The voice piece of the pack: the Kokoro model + voice bank."""
    return kokoro_ready()
