"""Piper voice + wake-word model family of core/voice/local_assets.py (skills split, batch 2)."""
from pathlib import Path
from ..config import DATA_DIR  # Phase 3 V2: config stays flat
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_model).
from .local_assets_model import (
    _bundled_child,
    _size,
)
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_proxy).
from .local_assets_proxy import (
    PIPER_VOICE_FILE,
    WAKE_MODEL_NAME,
    zip_bomb_refusal,
)


VOICES_DIR = DATA_DIR / "voices"


WAKE_DIR = VOICES_DIR / "vosk"


WAKE_MODEL_URL = f"https://alphacephei.com/vosk/models/{WAKE_MODEL_NAME}.zip"


_PIPER_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main"
    "/en/en_US/amy/medium"
)


PIPER_VOICE_URL = f"{_PIPER_BASE}/{PIPER_VOICE_FILE}"


PIPER_CONFIG_URL = f"{PIPER_VOICE_URL}.json"


MIN_WAKE_MODEL_BYTES = 50_000_000        # sanity floor on the EXTRACTED wake model tree: the real one unpacks to ~71 MB


MIN_PIPER_VOICE_BYTES = 50_000_000       # sanity floor: the real amy-medium ONNX is ~63 MB


def piper_paths() -> tuple[Path, Path]:
    """The RESOLVED Piper fallback voice (model, config): the
    bundled install copy wins when the pair is complete there
    (the same floor piper_ready() applies), else the copies
    under data/voices. A half-bundled pair never splits across
    roots — PiperVoice.load reads the config from beside the
    model file."""
    voice = VOICES_DIR / PIPER_VOICE_FILE
    config = VOICES_DIR / f"{PIPER_VOICE_FILE}.json"
    bundled_dir = _bundled_child("voices")
    if bundled_dir is not None:
        b_voice = bundled_dir / PIPER_VOICE_FILE
        b_config = bundled_dir / f"{PIPER_VOICE_FILE}.json"
        if _size(b_voice) > MIN_PIPER_VOICE_BYTES and b_config.exists():
            voice, config = b_voice, b_config
    return voice, config


def download_piper_paths() -> tuple[Path, Path]:
    """Where Piper (re)downloads land: always data/voices —
    the install tree is never written."""
    return (VOICES_DIR / PIPER_VOICE_FILE,
            VOICES_DIR / f"{PIPER_VOICE_FILE}.json")


def piper_ready() -> bool:
    voice, config = piper_paths()
    return _size(voice) > MIN_PIPER_VOICE_BYTES and config.exists()


def _wake_dir_plausible(path: Path) -> bool:
    """An extracted wake model folder counts when its acoustic model
    is present and the whole tree clears the size floor — a folder
    left half-unpacked by a killed run (the zip extracts in member
    order, big graph files late) never counts as ready."""
    from . import local_assets as _la  # INTENTIONAL: facade-at-call-time for batch-3 names
    return (
        path.is_dir()
        and _size(path / "am" / "final.mdl") > 1_000_000
        and _la._tree_bytes(path)[0] > MIN_WAKE_MODEL_BYTES
    )


def wake_model_path() -> Path:
    """The RESOLVED wake model folder: the bundled install copy wins
    when it is a plausible model (same rule as kokoro_paths), else
    the downloaded copy under data/voices/vosk."""
    bundled = _bundled_child("voices", "vosk", WAKE_MODEL_NAME)
    if bundled is not None and _wake_dir_plausible(bundled):
        return bundled
    return WAKE_DIR / WAKE_MODEL_NAME


def download_wake_model_path() -> Path:
    """Where the wake model (re)download unpacks: always
    data/voices/vosk (see download_model_path — the install tree is
    never written)."""
    return WAKE_DIR / WAKE_MODEL_NAME


def download_wake_zip_path() -> Path:
    """Where the wake model zip lands; download_file stages its
    ".part" beside it exactly like every other pack file."""
    return WAKE_DIR / f"{WAKE_MODEL_NAME}.zip"


def wake_model_ready() -> bool:
    """The wake piece of the voice stage: the Vosk model folder is
    unpacked and plausible — bundled or downloaded. The FINAL folder
    only: a zip or ".part" on disk never counts."""
    return _wake_dir_plausible(wake_model_path())


def _wake_zip_plausible(path: Path) -> bool:
    """A downloaded wake zip is worth unpacking when it is a real
    zip whose members live under the model folder. Corrupt or
    truncated bytes return False — they can never heal, so the
    caller deletes the zip and a later run re-downloads (the same
    philosophy as the brain integrity gate)."""
    try:
        import zipfile

        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        return any(name.startswith(WAKE_MODEL_NAME + "/") for name in names)
    except Exception:  # noqa: BLE001 — a bad zip is a False, never a crash
        return False


def _extract_wake_model(zip_path: Path) -> str | None:
    """Unpacks the wake zip into WAKE_DIR; None on success, else an
    error string. Zip-slip guarded: every member must resolve inside
    WAKE_DIR. Never raises."""
    import zipfile

    try:
        WAKE_DIR.mkdir(parents=True, exist_ok=True)
        base = WAKE_DIR.resolve()
        with zipfile.ZipFile(zip_path) as zf:
            # v5.47 (programme item 18): zip-bomb caps BEFORE any
            # member is written — the wake zip is downloaded from
            # the network, so its header is attacker-controlled
            # input exactly like a user-handed archive's.
            refusal = zip_bomb_refusal(zf.infolist(), _size(zip_path))
            if refusal is not None:
                return refusal
            for member in zf.infolist():
                target = (base / member.filename).resolve()
                if target != base and base not in target.parents:
                    return (f"wake model zip holds an unsafe path: "
                            f"{member.filename}")
            zf.extractall(WAKE_DIR)
    except Exception as e:  # noqa: BLE001 — reported by the caller
        return f"wake model unpack failed: {e}"
    if not wake_model_ready():
        return "wake model unpack finished but the model folder is incomplete"
    return None
