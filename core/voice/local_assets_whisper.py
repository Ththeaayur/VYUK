"""Whisper STT family of core/voice/local_assets.py (skills split, batch 2)."""
import os
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from ..config import config  # Phase 3 V2: config stays flat
from .. import config_spec
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_model).
from .local_assets_model import (
    _bundled_child,
    _size,
    total_ram_gb,
)
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_proxy).
from .local_assets_proxy import (
    _HF_MIRROR_HOST,
    _HF_PRIMARY_HOST,
    _arm_hf_mirror,
    download_file,
    sha256_of,
)
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_piperwake).
from .local_assets_piperwake import (
    MIN_PIPER_VOICE_BYTES,
    PIPER_CONFIG_URL,
    PIPER_VOICE_URL,
    WAKE_MODEL_URL,
    _extract_wake_model,
    _wake_zip_plausible,
    download_piper_paths,
    download_wake_zip_path,
    piper_paths,
    piper_ready,
    wake_model_path,
    wake_model_ready,
)


def _whisper_hub_dir() -> Path:
    """faster-whisper's weight cache: the Hugging Face hub directory
    (HUGGINGFACE_HUB_CACHE wins, then HF_HOME, then ~/.cache/huggingface)."""
    hub_cache = os.environ.get("HUGGINGFACE_HUB_CACHE")
    if hub_cache:
        return Path(hub_cache).expanduser()
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


MIN_WHISPER_BYTES = 10_000_000           # sanity floor: even the tiny Whisper model.bin is ~75 MB


WHISPER_SMALL_MODEL = "small"


WHISPER_TURBO_MODEL = "large-v3-turbo"


WHISPER_TURBO_REPO = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"


WHISPER_TURBO_MIN_RAM_GB = 16.0


WHISPER_TURBO_STAGED_DIR = "whisper-turbo"


WHISPER_MODEL_SHA256 = {
    WHISPER_SMALL_MODEL:
        "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
    WHISPER_TURBO_MODEL:
        "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
}


def whisper_model_sha256(model_name: str | None = None) -> str | None:
    """The SHA-256 pin for a Whisper model's model.bin (the
    EFFECTIVE model when none is named), or None when that model
    carries no pin — a custom WHISPER_MODEL pick has no measured
    hash on record, so it skips verification exactly like a
    custom brain GGUF skips the _download_once gate."""
    model = model_name or effective_whisper_model_name()
    return WHISPER_MODEL_SHA256.get(model) or None


def verify_whisper_snapshot(snapshot: Path | None,
                            model_name: str | None = None) -> str | None:
    """The post-download integrity verdict for one Whisper
    snapshot (v5.47, programme item 17): None when the snapshot's
    model.bin matches its pin (or the model carries no pin, or
    there is no model.bin to judge — readiness floors own that
    case), else the clean integrity error string the other
    integrity failures use.

    This function NEVER deletes anything: a bundled snapshot is
    read-only install content, so removal is the caller's call —
    _fetch_whisper_weights deletes a bad HUB-CACHE model.bin (the
    bytes it just downloaded) and only reports a bad bundled one.
    Never raises."""
    if snapshot is None:
        return None
    model = model_name or effective_whisper_model_name()
    pin = whisper_model_sha256(model)
    if pin is None:
        return None
    model_bin = Path(snapshot) / "model.bin"
    if not model_bin.is_file():
        return None
    try:
        actual = sha256_of(model_bin)
    except Exception:  # noqa: BLE001 — an unreadable file is not a verdict
        return None
    if actual == pin:
        return None
    return (
        f"download of ears (Whisper {model}) failed: the "
        "downloaded file failed its integrity check (SHA-256 "
        "mismatch) — the corrupt file was removed; downloading "
        "again is safe"
    )


def explicit_whisper_model() -> str | None:
    """The WHISPER_MODEL the user explicitly set (environment or
    .env — config.py loads .env into os.environ, so one probe
    covers both), or None when nothing was set and the config
    value is just the built-in default. Mirrors
    explicit_model_file(): a hand-set value is the user's pick."""
    value = (config_spec.explicit("WHISPER_MODEL") or "").strip()
    return value or None


def whisper_repo_for(model_name: str) -> str:
    """The Hugging Face repo a faster-whisper size's CT2 snapshot
    lives in: Systran's conversion for every standard size, the
    turbo conversion for large-v3-turbo (see the section header —
    no Systran turbo repo exists)."""
    if model_name == WHISPER_TURBO_MODEL:
        return WHISPER_TURBO_REPO
    return f"Systran/faster-whisper-{model_name}"


def _hub_repo_dir_name(repo: str) -> str:
    """The hub-cache directory name for a repo id ("a/b" ->
    "models--a--b") — the layout huggingface_hub writes."""
    return "models--" + repo.replace("/", "--")


def effective_whisper_model() -> dict:
    """The Whisper model the ears will use, and why (v5.45, A10).

    Returns {"model", "tier" ("small" | "turbo" | "custom"),
    "source" ("your pick" | "auto"), "ram_gb" (float or None),
    "repo"}. Pure reads — safe to call from the doctor and tests.
    Resolution rules are in the section header above."""
    ram_gb = total_ram_gb()
    picked = explicit_whisper_model()
    if picked:
        tier = ("turbo" if picked == WHISPER_TURBO_MODEL
                else "small" if picked == WHISPER_SMALL_MODEL
                else "custom")
        return {"model": picked, "tier": tier, "source": "your pick",
                "ram_gb": ram_gb, "repo": whisper_repo_for(picked)}
    tier_pick = str(
        getattr(config, "whisper_tier", "auto") or "auto").strip().lower()
    if tier_pick in ("turbo", WHISPER_TURBO_MODEL):
        return {"model": WHISPER_TURBO_MODEL, "tier": "turbo",
                "source": "your pick", "ram_gb": ram_gb,
                "repo": WHISPER_TURBO_REPO}
    if tier_pick == WHISPER_SMALL_MODEL:
        return {"model": WHISPER_SMALL_MODEL, "tier": "small",
                "source": "your pick", "ram_gb": ram_gb,
                "repo": whisper_repo_for(WHISPER_SMALL_MODEL)}
    if ram_gb is not None and ram_gb >= WHISPER_TURBO_MIN_RAM_GB:
        return {"model": WHISPER_TURBO_MODEL, "tier": "turbo",
                "source": "auto", "ram_gb": ram_gb,
                "repo": WHISPER_TURBO_REPO}
    return {"model": WHISPER_SMALL_MODEL, "tier": "small",
            "source": "auto", "ram_gb": ram_gb,
            "repo": whisper_repo_for(WHISPER_SMALL_MODEL)}


def effective_whisper_model_name() -> str:
    """The model name of effective_whisper_model() — the one name
    the provisioning fetch and the readiness checks agree on."""
    return effective_whisper_model()["model"]


def _hub_whisper_snapshot(model_name: str) -> Path | None:
    """The hub-cache snapshot folder for `model_name` whose
    model.bin clears the sanity floor, or None."""
    repo_dir = _whisper_hub_dir() / _hub_repo_dir_name(
        whisper_repo_for(model_name))
    snapshots = repo_dir / "snapshots"
    if not snapshots.is_dir():
        return None
    for snapshot in sorted(snapshots.iterdir()):
        if _size(snapshot / "model.bin") > MIN_WHISPER_BYTES:
            return snapshot
    return None


def whisper_snapshot_path(model_name: str | None = None) -> Path | None:
    """A local snapshot folder for the model (the effective one
    when none is named) that WhisperModel can load directly —
    the bundled install copy first, then the hub-cache snapshot.
    This is the ONE helper the transcription load sites resolve
    through: bundled_whisper_path() (which
    core/voice/local_client.py's _ensure_whisper already reads) is its
    bundled half, and whisper_model_arg() below wraps the whole
    resolution into the single WhisperModel argument."""
    model = model_name or effective_whisper_model_name()
    bundled = bundled_whisper_path(model)
    if bundled is not None:
        return bundled
    return _hub_whisper_snapshot(model)


def whisper_model_arg(model_name: str | None = None) -> str:
    """The argument WhisperModel should be constructed with for
    the model (the effective one when none is named): the local
    snapshot path when one is on disk (a path bypasses the hub
    entirely), else the model NAME — constructing with the name
    is what makes faster-whisper download the weights, which is
    how the provisioning fetch below lands them."""
    model = model_name or effective_whisper_model_name()
    snapshot = whisper_snapshot_path(model)
    return str(snapshot) if snapshot is not None else model


def bundled_whisper_path(model_name: str | None = None) -> Path | None:
    """The bundled faster-whisper snapshot folder for the model
    (the EFFECTIVE one when none is named — v5.45, A10), when the
    install carries one — the directory WhisperModel should load
    directly (a local path bypasses the Hugging Face hub
    entirely). Layouts recognised:
      - the plain snapshot folder the packaging scripts stage —
        <assets>/whisper/ for small (model.bin + config.json +
        tokenizer.json + vocabulary.txt at its root),
        <assets>/whisper-turbo/ for the turbo tier; and
      - an HF hub subtree under <assets>/whisper/
        (models--<repo>/snapshots/<rev>/…) for hand-assembled
        installs.
    The snapshot's model.bin must clear the same sanity floor
    whisper_ready() applies to the hub cache."""
    model = model_name or effective_whisper_model_name()
    if model == WHISPER_TURBO_MODEL:
        plain_dir = _bundled_child(WHISPER_TURBO_STAGED_DIR)
    elif model == WHISPER_SMALL_MODEL:
        plain_dir = _bundled_child("whisper")
    else:
        plain_dir = None
    if plain_dir is not None and plain_dir.is_dir():
        if _size(plain_dir / "model.bin") > MIN_WHISPER_BYTES:
            return plain_dir
    whisper_dir = _bundled_child("whisper")
    if whisper_dir is None or not whisper_dir.is_dir():
        return None
    repo_dir = whisper_dir / _hub_repo_dir_name(whisper_repo_for(model))
    snapshots = repo_dir / "snapshots"
    if snapshots.is_dir():
        for snapshot in sorted(snapshots.iterdir()):
            if _size(snapshot / "model.bin") > MIN_WHISPER_BYTES:
                return snapshot
    return None


def whisper_ready() -> bool:
    """The ears piece of the pack: faster-whisper's weights for
    the EFFECTIVE model size (v5.45, A10 — the tier resolution
    above) are available — bundled with the install (v4.0, see
    bundled_whisper_path()) or in the Hugging Face cache. The
    weights live in a `models--<repo>` snapshot whose model.bin
    must clear the sanity floor (tests monkeypatch the floor).
    Read-only — this never triggers a download."""
    return whisper_snapshot_path() is not None


def _fetch_whisper_weights(emit: Callable[[str], None]) -> None:
    """Downloads the Whisper weights (the ears) if they are missing.

    faster-whisper fetches its own weights the first time a
    WhisperModel is constructed, so "downloading the ears" means
    constructing one, briefly, and letting go. Never raises and never
    fails the caller: when the package is missing, or the fetch fails
    (offline first run), a plain note is emitted instead of an ERROR
    line — the ears retry on first real use (_ensure_whisper in
    core/voice/local_client.py degrades to typed input), while the rest of
    the pack continues downloading.

    v5.45 (A10): the model fetched is the EFFECTIVE one
    (effective_whisper_model()) — on a >=16 GB machine this stage
    provisions large-v3-turbo (a resumable hub download of its
    snapshot, performed by faster-whisper/huggingface_hub itself,
    exactly as the small snapshot always was); below that, small.

    v5.47 (programme item 17): the landed snapshot's model.bin is
    SHA-256-verified against WHISPER_MODEL_SHA256 — both when it
    was already on disk at entry and right after the fetch — via
    verify_whisper_snapshot(); a bad hub-cache file is deleted
    and re-fetched, a bad bundled file is reported, never
    deleted, and never declared ready.
    """
    from . import local_assets as _la  # INTENTIONAL: facade-at-call-time for batch-3 names
    model_name = effective_whisper_model_name()
    if whisper_ready():
        # v5.47 (programme item 17): a snapshot that clears the
        # size floor still has to pass its hash pin before it is
        # declared ready — this provisioning run is the ears'
        # download-completion gate (see WHISPER_MODEL_SHA256).
        # A bad HUB-CACHE model.bin is deleted and re-fetched
        # below (its bytes can never heal); a bad BUNDLED copy is
        # install content — reported, never deleted, and never
        # declared ready.
        snap = whisper_snapshot_path(model_name)
        verr = (verify_whisper_snapshot(snap, model_name)
                if snap is not None else None)
        if verr is None:
            _la._progress_update("whisper", done=_la._whisper_cache_bytes(),
                             state="done")
            emit(f"Ears (Whisper {model_name}) ready.")
            return
        print(f"[LocalAssets] Whisper integrity failure: {verr}")
        _la._progress_update("whisper", state="error", error=verr)
        if snap is not None and snap == bundled_whisper_path(model_name):
            emit(
                f"Ears (Whisper {model_name}) failed their integrity "
                "check — the bundled copy is corrupt; voice input "
                "will retry when you connect; typing works meanwhile."
            )
            return
        try:
            (snap / "model.bin").unlink()
        except (OSError, TypeError):
            pass
        # fall through: re-download the weights fresh
    # Structured progress for the ears is measured, not declared:
    # faster-whisper exposes no byte counts, so the tracker records
    # the cache dir's size before/after and pack_progress() reports
    # the stage's total as unknown until the weights have landed.
    _la._progress_begin("whisper", total=None, done=_la._whisper_cache_bytes())
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        _la._progress_update("whisper", done=_la._whisper_cache_bytes(), state="waiting")
        emit(
            f"Ears (Whisper {model_name}) fetch their own weights "
            "on first use — nothing to download for them here."
        )
        return
    emit(f"Downloading ears (Whisper {model_name}) — first run only…")

    def _construct() -> Exception | None:
        # Field-check fix #18 (M10c): the same bounded wait the
        # turn path uses — a stalled hub download must not hang
        # setup forever. The construction itself is unchanged (this
        # stays the provisioning path's own site); only the wait is
        # bounded, via the shared deadline helper. A timeout comes
        # back as an error, so the existing mirror retry treats it
        # like any other "the primary didn't answer" failure.
        from .asr import (  # INTENTIONAL: function-local, like the _la facade
            WHISPER_LOAD_TIMEOUT_S,
            _call_with_timeout,
        )
        model, error, timed_out = _call_with_timeout(
            lambda: WhisperModel(model_name, device="cpu",
                                 compute_type="int8"),
            WHISPER_LOAD_TIMEOUT_S)
        if timed_out:
            return TimeoutError(
                f"Whisper model load timed out after "
                f"{WHISPER_LOAD_TIMEOUT_S}s ({model_name})")
        if error is not None:
            return error
        del model
        return None

    error = _construct()
    if error is not None and _arm_hf_mirror():
        # v3.3: huggingface.co didn't answer the ears either — one
        # retry with the hub endpoint pointed at hf-mirror.com. The
        # attempt is recorded either way ("via"), so a later failure
        # report shows the mirror was tried, not just the primary.
        _la._progress_update("whisper", via=_HF_MIRROR_HOST)
        emit("Ears download didn't answer — retrying via the "
             "hf-mirror.com mirror…")
        error = _construct()
    if error is not None:  # noqa: BLE001 — ears must never block the pack
        print(f"[LocalAssets] Whisper weight download failed: {error}")
        _la._progress_update(
            "whisper", done=_la._whisper_cache_bytes(), state="waiting",
            error=f"download of ears (Whisper {model_name}) "
                  f"failed: {error}",
        )
        emit(
            "Ears couldn't download just now — voice input will retry "
            "when you connect; typing works meanwhile."
        )
        return
    # v5.47 (programme item 17): POST-DOWNLOAD VERIFY — the fetch
    # above is faster-whisper's own, so this is the first moment
    # VYUK can judge the bytes that landed. A mismatch deletes the
    # bad hub-cache model.bin (never a bundled copy — a bundled
    # snapshot would have counted as ready above and returned
    # before any construct) and surfaces the integrity error
    # through this stage's usual plumbing (console line + the
    # tracker asset's error, quoted by the doctor); the emitted
    # line keeps the ears' note-not-ERROR posture, like every
    # other failure in this function.
    snap = whisper_snapshot_path(model_name)
    verr = (verify_whisper_snapshot(snap, model_name)
            if snap is not None else None)
    if verr is not None:
        try:
            (snap / "model.bin").unlink()
        except (OSError, TypeError):
            pass
        print(f"[LocalAssets] Whisper integrity failure: {verr}")
        _la._progress_update(
            "whisper", done=_la._whisper_cache_bytes(), state="error",
            error=verr,
        )
        emit(
            "Ears couldn't be verified just now — voice input will "
            "retry when you connect; typing works meanwhile."
        )
        return
    endpoint = (os.environ.get("HF_ENDPOINT") or "").strip()
    served_by = (urllib.parse.urlsplit(endpoint).hostname
                 if endpoint else _HF_PRIMARY_HOST)
    _la._progress_update(
        "whisper",
        done=_la._whisper_cache_bytes(),
        state="done" if whisper_ready() else "waiting",
        via=served_by,
    )
    if whisper_ready():
        emit(f"Ears (Whisper {model_name}) ready.")
    else:
        emit(
            f"Ears (Whisper {model_name}) fetched — "
            "they finish setting up on first use."
        )


def _fetch_piper_voice(emit: Callable[[str], None],
                       cancel_event: threading.Event | None = None) -> None:
    """Downloads the Piper fallback voice if it is missing.

    Rides the voice stage (same downloader, same .part
    staging) with the wake model's FAILURE POSTURE: Piper is
    a fallback behind Kokoro, so a failure here never emits
    an ERROR line and never fails the run — it leaves a plain
    note, records the error on the tracker asset, and is
    retried on the next setup run while missing. Never
    raises."""
    from . import local_assets as _la  # INTENTIONAL: facade-at-call-time for batch-3 names
    if piper_ready():
        voice, _config = piper_paths()
        _la._progress_update("piper_voice", done=_size(voice),
                         state="done")
        return
    dl_voice, dl_config = download_piper_paths()
    res_voice, res_config = piper_paths()
    if _size(res_voice) <= MIN_PIPER_VOICE_BYTES:
        err = download_file(PIPER_VOICE_URL, dl_voice, emit,
                            "fallback voice (Piper)", cancel_event,
                            progress_asset="piper_voice")
        if err == "CANCELLED":
            return  # the caller's cancelled() check pauses the run
        if err is not None:
            print(f"[LocalAssets] Piper voice download failed: {err}")
            emit("Fallback voice (Piper) didn't download just now — "
                 "the main voice carries on; setup retries it next run.")
            return
    if not res_config.exists():
        err = download_file(PIPER_CONFIG_URL, dl_config, emit,
                            "fallback voice config (Piper)",
                            cancel_event, progress_asset="piper_config")
        if err == "CANCELLED":
            return
        if err is not None:
            print(f"[LocalAssets] Piper config download failed: {err}")
            emit("Fallback voice (Piper) didn't download just now — "
                 "the main voice carries on; setup retries it next run.")
            return
    if piper_ready():
        _la._progress_update("piper_voice", done=_size(dl_voice),
                         state="done")


def _fetch_wake_model(emit: Callable[[str], None],
                      cancel_event: threading.Event | None = None) -> None:
    """Downloads + unpacks the wake-word model if it is missing.

    The wake model rides the voice stage — same downloader, same
    ".part" staging, tracked as the "wake_model" asset — but its
    FAILURE POSTURE is the ears' (see _fetch_whisper_weights): a
    failure here never emits an ERROR line and never fails the run.
    VYUK works fully without hands-free wake (the detector falls
    back to its older triggers), so one dead route to a 41 MB file
    must not mark a multi-GB pack failed or stall its retry cadence.
    The failure is still VISIBLE: a plain note in the run's lines,
    the error download_file recorded on the tracker asset, and a
    console log line. Retried on every setup run while missing.
    Never raises.
    """
    from . import local_assets as _la  # INTENTIONAL: facade-at-call-time for batch-3 names
    if wake_model_ready():
        _la._progress_update("wake_model",
                         done=_la._tree_bytes(wake_model_path())[0],
                         state="done")
        return
    zip_dest = download_wake_zip_path()
    if not _wake_zip_plausible(zip_dest):
        err = download_file(WAKE_MODEL_URL, zip_dest, emit,
                            "wake-word model (Vosk)", cancel_event,
                            progress_asset="wake_model")
        if err == "CANCELLED":
            return  # the caller's cancelled() check pauses the run
        if err is not None:
            print(f"[LocalAssets] Wake model download failed: {err}")
            emit("Wake-word model didn't download just now — 'Hey VYUK' "
                 "stays on the basic trigger until it lands; setup "
                 "retries it next run.")
            return
    # A plausible zip is on disk (just downloaded, or left by a run
    # that died between download and unpack): unpack it.
    err = _extract_wake_model(zip_dest)
    if err is not None:
        print(f"[LocalAssets] {err}")
        _la._progress_update("wake_model", state="error", error=err)
        try:
            zip_dest.unlink()  # corrupt bytes can never heal — drop them
        except OSError:
            pass
        emit("Wake-word model downloaded but couldn't be unpacked — "
             "'Hey VYUK' stays on the basic trigger until it lands; "
             "setup retries it next run.")
        return
    try:
        zip_dest.unlink()  # unpacked — the zip itself is dead weight
    except OSError:
        pass
    _la._progress_update("wake_model",
                     done=_la._tree_bytes(wake_model_path())[0], state="done")
    emit("Wake-word model ready ('Hey VYUK').")
