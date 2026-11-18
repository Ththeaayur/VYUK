"""
Asset management for VYUK's built-in local brain (v1.8; smart
downloads v2.0).

The zero-setup promise lives here: everything the built-in backend
needs beyond `pip install -r requirements.txt` is a data file, and
this module fetches it on first use —

    data/models/<LOCAL_MODEL_FILE>     the LLM (Qwen3-4B-Instruct-2507
                                       GGUF by default — Balanced tier,
                                       ~2.5 GB, Apache-2.0; Lite
                                       (Qwen3-1.7B, ~1.1 GB) and Quality
                                       (Qwen3-8B, ~5 GB) tiers flank it)
    data/voices/kokoro/kokoro-v1.0.onnx
    data/voices/kokoro/voices-v1.0.bin the Kokoro-82M voice (Apache-2.0)
    data/voices/vosk/vosk-model-small-en-us-0.15/
                                       the wake-word model (Vosk
                                       small en-us, Apache-2.0),
                                       unpacked from its ~41 MB zip
                                       by the voice stage (v5.2)

Downloads are RESUMABLE (v2.0): bytes land in a sibling ".part" file,
an interrupted download keeps its .part, and the next attempt sends an
HTTP Range request and continues where it left off. A download can
also be cancelled cooperatively via a threading.Event — the partial
.part is kept, ready to resume. The `downloads` DownloadManager
singleton runs ensure_local_assets() on a background thread for the
Settings UI; storage_report() / remove_offline_pack() account for and
reclaim the pack's disk space.

DOWNLOAD ORDER (v2.7, "progressively alive"): ensure_local_assets()
fetches the pack in usefulness order — the ears (faster-whisper
weights) first, then the voice (Kokoro), then the brain (the GGUF) —
so every finished step makes a partially-downloaded pack strictly
more capable, and core/voice/lite_client.py can serve a live session off
whatever has landed so far. Readiness for each piece is a public
helper: whisper_ready() / voice_ready() / brain_ready().

TRUTHFUL PROGRESS + RAM-SIZED TIERS (v3.2): alongside the text lines,
every download records structured byte counts (see pack_progress()),
so the setup banner, onboarding, lite session and doctor quote real
numbers instead of a percentage scraped out of a status line. And
which GGUF gets downloaded is resolved by effective_model(): the
user's explicit pick wins, then a tier already on disk, then a tier
sized to this PC's RAM (≤10 GB Lite, ≤20 GB Balanced, above Quality).

DOWNLOAD RESILIENCE (v3.3): the transport itself hardened after a
real first run on a campus network where the pack would not download
at all. Downloads now go through the machine's SYSTEM PROXY when one
is configured (the proxy browsers use — read via
urllib.request.getproxies(); Python's defaults could miss it, and a
proxy is the whole difference between "works in the browser" and
"dead in the app"). A failed huggingface.co download retries once
against hf-mirror.com (same path, same bytes, same .part resume), and
the Whisper fetch retries once with HF_ENDPOINT pointed at the
mirror. Failures are VISIBLE: the stage's last error is recorded in
pack_progress() and persisted (data/download_state.json) so the
doctor can quote it after a restart, and a failed run is not
auto-retried at every launch — auto_start_allowed() enforces a
10-minute cadence after a failure.

BUNDLED ASSETS (v4.0, "VYUK in a box"): the installer ships the pack
INSIDE the install, so a first run needs no download at all. When a
bundled assets root exists (see bundled_assets_dir(): the
NOVA_ASSETS_DIR env var, <exe_dir>/assets in a frozen build, or an
assets/ folder staged beside the package in dev), resolution prefers
it — a bundled brain/voice/ears wins over the downloaded copies in
data/, which remain the repair/upgrade path (downloads still land in
data/, and remove_offline_pack() never touches the install tree).
With no bundled root, every resolution below is exactly the pre-v4.0
behaviour.

Licences for every component are summarised in THIRD_PARTY_LICENSES.md
at the package root; everything auto-downloaded is Apache-2.0 / MIT.

No heavy imports at module level (stdlib + core.config only), and no
function in this module raises at its caller: download_file() returns
None on success, the sentinel "CANCELLED" on cancellation, or an
error string, and ensure_local_assets() returns a list of human
progress lines whose failures start with "ERROR:".
"""

import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ..config import DATA_DIR, config  # Phase 3 V2: config stays flat

# INTENTIONAL: batch-1 re-exports (model family).
from .local_assets_model import (
    MODELS_DIR,
    MIN_MODEL_BYTES,
    MODEL_TIERS,
    DEFAULT_MODEL_URL,
    _package_root,
    _size,
    auto_tier_for_ram,
    brain_ready,
    bundled_assets_dir,
    bundled_model_path,
    download_model_path,
    effective_idle_unload_s,
    effective_model,
    effective_model_file,
    effective_n_ctx,
    explicit_model_file,
    explicit_n_ctx,
    model_is_bundled,
    model_path,
    model_ready,
    model_tier_for,
    model_url_for,
    total_ram_gb,
    _bundled_child,
    _existing_tier_file,
)

# INTENTIONAL: batch-1 re-exports (proxy-download family).
from .local_assets_proxy import (
    DOWNLOAD_SHA256,
    HF_MIRROR_BASE,
    PIPER_VOICE_FILE,
    WAKE_MODEL_NAME,
    ZIP_MAX_COMPRESSION_RATIO,
    ZIP_MAX_MEMBERS,
    ZIP_MAX_UNCOMPRESSED_BYTES,
    _CHUNK_BYTES,
    _DOWNLOAD_TIMEOUT_S,
    _HF_MIRROR_HOST,
    _HF_PRIMARY_HOST,
    _INTEGRITY_PREFIX,
    _arm_hf_mirror,
    _build_opener,
    _download_once,
    _human_size,
    _integrity_error,
    _merged_proxies,
    _mirror_variant,
    _open_response,
    _proxy_description,
    _tier_sha256,
    _usable_proxy,
    download_file,
    sha256_of,
    zip_bomb_refusal,
)

# INTENTIONAL: batch-2 re-exports (kokoro family).
from .local_assets_kokoro import (
    KOKORO_DIR,
    _KOKORO_RELEASE,
    KOKORO_MODEL_URL,
    KOKORO_VOICES_URL,
    MIN_KOKORO_MODEL_BYTES,
    MIN_KOKORO_VOICES_BYTES,
    kokoro_paths,
    download_kokoro_paths,
    kokoro_ready,
    voice_ready,
)
# INTENTIONAL: batch-2 re-exports (local family).
from .local_assets_vlm import (
    VLM_MODEL_FILE,
    VLM_MMPROJ_FILE,
    _VLM_REPO,
    VLM_MODEL_URL,
    VLM_MMPROJ_URL,
    VLM_MODEL_SHA256,
    VLM_MMPROJ_SHA256,
    MIN_VLM_MODEL_BYTES,
    MIN_VLM_MMPROJ_BYTES,
    vlm_paths,
    download_vlm_paths,
    vlm_ready,
    vlm_explicit_pick,
    vlm_tier_allowed,
    vlm_status,
    ensure_vlm_assets,
    _fetch_vlm_model,
)
# INTENTIONAL: batch-2 re-exports (tesseract family).
from .local_assets_tesseract import (
    tesseract_path,
    configure_pytesseract,
    OCR_TESSERACT_CONFIG,
    _HIN_MISSING_NOTE,
    _tessdata_candidate_dirs,
    _dir_languages,
    tessdata_dir,
    tessdata_languages,
    resolve_ocr_language,
    ocr_tesseract_config,
)
# INTENTIONAL: batch-2 re-exports (piper family).
from .local_assets_piperwake import (
    VOICES_DIR,
    WAKE_DIR,
    WAKE_MODEL_URL,
    _PIPER_BASE,
    PIPER_VOICE_URL,
    PIPER_CONFIG_URL,
    MIN_WAKE_MODEL_BYTES,
    MIN_PIPER_VOICE_BYTES,
    piper_paths,
    download_piper_paths,
    piper_ready,
    _wake_dir_plausible,
    wake_model_path,
    download_wake_model_path,
    download_wake_zip_path,
    wake_model_ready,
    _wake_zip_plausible,
    _extract_wake_model,
)
# INTENTIONAL: batch-2 re-exports (whisper family).
from .local_assets_whisper import (
    _whisper_hub_dir,
    MIN_WHISPER_BYTES,
    WHISPER_SMALL_MODEL,
    WHISPER_TURBO_MODEL,
    WHISPER_TURBO_REPO,
    WHISPER_TURBO_MIN_RAM_GB,
    WHISPER_TURBO_STAGED_DIR,
    WHISPER_MODEL_SHA256,
    whisper_model_sha256,
    verify_whisper_snapshot,
    explicit_whisper_model,
    whisper_repo_for,
    _hub_repo_dir_name,
    effective_whisper_model,
    effective_whisper_model_name,
    _hub_whisper_snapshot,
    whisper_snapshot_path,
    whisper_model_arg,
    bundled_whisper_path,
    whisper_ready,
    _fetch_whisper_weights,
    _fetch_piper_voice,
    _fetch_wake_model,
)

# INTENTIONAL (batch 3): re-exports (progress family).
from .local_assets_progress import (
    _ASSET_STAGE,
    _STAGE_ASSETS,
    _STAGE_ORDER,
    _ASSET_FLOORS,
    _progress_lock,
    _progress_assets,
    _progress_run_state,
    _progress_begin,
    _KEEP,
    _trim_error,
    _progress_update,
    _progress_set_run_state,
    _STATE_FILE_NAME,
    _dl_state_lock,
    _dl_state,
    AUTO_RETRY_AFTER_S,
    _download_state_path,
    _download_state,
    _save_download_state,
    last_download_error,
    _record_download_failure,
    _clear_download_failure,
    _record_served_by,
    last_download_info,
)

# INTENTIONAL (batch 3): re-exports (diagnose_pack family).
from .local_assets_diagnose_pack import (
    _probe_host,
    _diagnose_proxy_line,
    _diagnose_disk_line,
    diagnose_downloads,
    auto_start_allowed,
    _whisper_cache_bytes,
    _file_or_part_size,
    pack_progress,
    format_bytes,
    _tree_bytes,
    storage_report,
    remove_offline_pack,
)

# INTENTIONAL (batch 3): re-exports (ensure family).
from .local_assets_ensure import (
    _ENSURE_LOCK,
    ensure_local_assets,
    _ensure_local_assets_serialized,
    DownloadManager,
    downloads,
)


# --- Where things live -------------------------------------------------


# --- Bundled assets (v4.0 — "VYUK in a box") ----------------------------
# The installer carries the pack with it: an assets root holding
#     models/            tier GGUF(s)
#     voices/kokoro/     kokoro-v1.0.onnx + voices-v1.0.bin
#     voices/            en_US-amy-medium.onnx (+ .json) — Piper fallback (v5.35)
#     voices/vosk/       vosk-model-small-en-us-0.15/ (wake word)
#     whisper/           the faster-whisper snapshot (plain folder)
# When such a root exists it WINS over data/ for every read (it is
# the install's own content — verified at build time, never written
# by the app). data/ stays the download/repair path: downloads still
# land there, and remove_offline_pack() only ever deletes from there.


# --- What to fetch ------------------------------------------------------
# Brain model tiers (v1.9; lite added v2.0). The tier is resolved by
# FILENAME: whatever LOCAL_MODEL_FILE names, the matching tier (if
# any) supplies the download URL. Auto-download covers exactly these
# three files; any other filename is a custom model the user places
# by hand (see ensure_local_assets). All are Qwen3-Instruct GGUFs at
# Q4_K_M — Apache-2.0 model family.
#
# VERIFIED LIVE (v4.0/v4.1, against the HF API): Qwen never
# published a Qwen3-4B-Instruct-2507-GGUF repo, and Qwen's own
# Qwen3-1.7B-GGUF ships ONLY the Q8_0 quant — so Balanced and Lite
# resolve through unsloth's repos (same official weights, the
# Q4_K_M files the table names). Quality's URL serves its file
# under the shorter Qwen3-8B-Q4_K_M.gguf name (it is downloaded to
# the tabled filename). If a tier download 404s, fix the single
# entry here — it is the only place each pair lives. A failed
# download surfaces as a clean ERROR: line from
# ensure_local_assets, never a crash.
# Each tier also carries the publisher's SHA-256 (v4.2 — the same
# hashes packaging/assets_manifest.json records): a completed brain
# download is verified against it before the file counts as ready
# (see the integrity gate in _download_once).

# The default tier's URL, kept as a plain constant for external
# references (docs, doctor, debugging) that just want "the model URL".


# --- Effective model resolution (v3.2: RAM-sized tiers) -----------------
# Which GGUF the built-in brain actually uses is resolved HERE, once,
# for every consumer (the download, readiness, the llama load, the
# doctor):
#   1. A file the user explicitly chose (LOCAL_MODEL_FILE is set in
#      the environment / .env) always wins — it is their pick, custom
#      files included.
#   2. A tier model already fully downloaded on disk wins next —
#      silently switching tiers would re-download gigabytes.
#   3. Otherwise the tier is sized to this PC's RAM: an 8B brain in an
#      8 GB machine is how a first run ends up swapping, so ≤10 GB
#      gets Lite, ≤20 GB Balanced, above that Quality. No psutil (or
#      an unreadable total) falls back to Balanced.


# --- RAM-aware engine knobs (v4.1, "runs light") -----------------------
# The tier table sizes the BRAIN to the PC; these two helpers size
# the brain's RUNTIME footprint the same way, so an 8 GB machine —
# the standard Windows laptop this profile targets — runs VYUK
# without squeezing Windows itself:
#   * the context window: the KV cache grows linearly with n_ctx,
#     so ≤10 GB of RAM halves it (2048) — still ample for the
#     12-turn history window the session sends;
#   * the idle unload: RAM is reclaimed after 5 quiet minutes
#     instead of 10 where RAM is scarcest.
# Both resolve HERE, next to total_ram_gb(), so the session, the
# doctor and tests all read the same numbers. Pure reads; never
# raise (an unreadable RAM total just yields the roomy defaults).


# Kokoro-82M ONNX bundle (kokoro-onnx "model-files-v1.0" release).

# Wake-word model (v5.2): Vosk small en-us, served as a zip by the
# project's own host (alphacephei.com — no mirror twin, like the
# GitHub files above). The zip unpacks to the model folder
# WAKE_MODEL_NAME; core/voice/wake_word.py loads the EXTRACTED folder.

# Piper fallback voice (v5.35): the middle rung of the local
# voice chain (Kokoro -> Piper -> SAPI) expected a file in
# data/voices/ that nothing provisioned — it only ever
# existed where a user dropped it by hand, so on a fresh
# install the rung silently never existed. It is a
# VOICE-stage asset like Kokoro: the rhasspy project's
# piper-voices repo serves the model and its config side by
# side; config.local_tts_voice names the default file.

# Download hash pins (v5.47, programme item 17 / C6b): until this
# wave only the brain GGUFs (MODEL_TIERS) and the VLM pair carried
# SHA-256 pins; every other pack download was accepted on a size
# floor alone, so a same-size corrupted or substituted file counted
# as ready. This map is keyed by DESTINATION FILENAME and consulted
# by the same _download_once integrity gate as the tier hashes
# (see _tier_sha256 below) — a completed download whose bytes hash
# wrong is deleted and reported, never promoted into place.
#
# PROVENANCE (the discipline that makes a pin worth anything):
# every hash below was computed with sha256sum from the REAL file
# as downloaded from its URL above on 2026-10-23 — never copied
# from a publisher page or an API digest. That matters here
# because several of these assets have NO publisher digest at all
# (the GitHub kokoro-onnx release publishes none, alphacephei
# publishes none for the Vosk zip, and the Piper config JSON is a
# non-LFS file with no LFS hash) — the pin exists precisely
# because VYUK measured the genuine bytes itself. The Vosk pin
# covers the downloaded ZIP file itself, before extraction (the
# extraction is separately capped — programme item 18).

# Sanity floors: a "ready" file must be at least this big, so a truncated
# or placeholder file never counts as downloaded. Tests monkeypatch these.


# --- Download transport (v3.3): system proxy + mirror fallback ---------
# A campus/hostel network is the hostile case for this module: the
# browser works because it uses the Windows SYSTEM PROXY, while a
# Python process that never asks can sit behind a dead direct route.
# So every download opens through an opener built from
# urllib.request.getproxies() (which reads the registry Internet
# Settings + the environment), with explicit HTTP(S)_PROXY env vars
# winning where set. And when huggingface.co itself won't answer,
# the same file is retried once against hf-mirror.com — a full
# Hugging Face mirror serving the same bytes at the same paths.


# --- Single-file download ----------------------------------------------


# --- Zip-bomb caps (v5.47, programme item 18 / C6c) ---------------------
# Every place VYUK EXTRACTS a zip used to trust the archive's own
# header: a zip bomb declares gigabytes (or worse) of output in a
# tiny file, and extractall() would happily try to write it all —
# filling the disk from a single "model" or "archive" the user was
# handed. The caps below are checked from the ZipInfo list BEFORE
# any member is written, at BOTH extraction sites: the wake-model
# unpack in this module (_extract_wake_model) and the user-facing
# unzip tool in core/desktop/control_plus.py (which imports this helper —
# control_plus -> local_assets -> config, no cycle). core/senses.py
# only READS an infolist for a sniff report and never extracts, so
# it needs no cap; core/health/updater.py's staging extraction is another
# builder's file in this wave and is flagged in the wave report.
#
# The caps are generous for every legitimate archive VYUK meets —
# the real wake zip is ~41 MB in, ~71 MB out, ratio ~1.7, a few
# hundred members — so only a hostile or corrupt archive trips
# them. They are module-level constants on purpose: tests
# monkeypatch them down to exercise the refusals with small
# fixture zips instead of multi-GB ones.


# Sentinel prefix on the error string _download_once returns for a
# completed-but-corrupt pinned download. download_file recognises it
# and does NOT mirror-retry: the bytes themselves failed
# verification, so the failure goes straight to the caller's error
# plumbing (stage error + persisted last_error + run state "failed")
# and the 600 s auto-retry cadence governs the next attempt. The
# prefix is stripped before the error reaches any caller.


# --- Readiness ----------------------------------------------------------


# --- Local vision-language model (v5.45, EYES E5b) ----------------------
# The local brain's eyes: a small vision-language model that lets
# the BUILT-IN brain describe a shared frame's actual pixels
# instead of only its OCR text (core/voice/local_client.py's
# describe_frame_vlm consumes it). The pair is:
#   data/models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf   (~1.93 GB)
#   data/models/mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf (~845 MB)
# from ggml-org's GGUF repo (the llama.cpp project's own repo;
# Apache-2.0) — sizes and SHA-256s below were read from the HF
# API's blob metadata when this section was written, the same
# provenance discipline as MODEL_TIERS.
#
# TIER GATE (the whole point of the item): this pair is a
# QUALITY-TIER extra. It is NEVER auto-downloaded below the
# Quality tier: ensure_vlm_assets() refuses unless the EFFECTIVE
# brain tier (effective_model()) is "quality" OR the user
# explicitly picked the asset — config.local_vlm / env LOCAL_VLM
# set to a truthy value (the Settings explicit pick writes that
# env key; see vlm_explicit_pick()). The installer mirrors the
# gate: packaging/assets_manifest.json stages both files under
# the brain-quality component only.
# Sanity floors (tests monkeypatch these): the real files are
# 1,929,901,056 and 844,757,728 bytes respectively.


# --- OCR engine (v5.35) ------------------------------------------------
# Tesseract is a BINARY, not a model: the installer carries a
# portable tree (assets/ocr/) the way it carries the models,
# and resolution follows the same house order — bundled
# install copy, then a provisioned copy under data/ocr,
# then PATH (a developer's own install). OCR used to see
# PATH only, so the engine the installer brings was
# invisible to it and a fresh install's OCR was simply dead.


# --- OCR languages (v5.44, E2) -------------------------------------------
# The engine is only half of OCR: Tesseract reads a language from
# *.traineddata files, and until now only the engine's own English
# was ever reachable — Hindi screens were unreadable no matter what
# the user's preferred language said. hin.traineddata (tessdata_fast)
# is BUNDLED beside eng.traineddata under assets/ocr/tessdata/ on a
# real install, exactly the way the engine itself is bundled under
# assets/ocr/; a provisioned copy under data/ocr/ is the repair
# path, mirroring tesseract_path()'s order above.


# --- Wake-word model (v5.2) ---------------------------------------------
# The Vosk small en-us model that turns "Hey VYUK" from an energy
# guess into grammar-constrained keyword spotting (core/voice/wake_word.py).
# It is a VOICE-stage asset: it lives beside Kokoro under data/voices,
# downloads through the same downloader and is tracked as the
# "wake_model" progress asset. Unlike Kokoro it arrives as a ZIP that
# must be unpacked — the recognizer loads the extracted folder.


# --- Progressive-readiness helpers (v2.7) --------------------------------
# One question per pack piece — ears / voice / brain — so the lite
# session (core/voice/lite_client.py) and core/brain/brain.py can ask "what has
# landed so far?" without knowing file layouts. Same contract as the
# readiness checks above: FINAL files only (never ".part" resume
# state), gated on size floors so a truncated file never counts.


# --- Whisper STT tiers (v5.45, EARS A10) --------------------------------
# The ears get the brain tiers' treatment: which Whisper model
# transcribes is RESOLVED here, once, for every consumer (the
# bundled/hub snapshot lookup, the provisioning fetch, the
# doctor):
#   1. A model the user explicitly chose (WHISPER_MODEL set in
#      the environment / .env) always wins — it is their pick,
#      custom sizes included, exactly as LOCAL_MODEL_FILE wins
#      over the brain tiers in effective_model().
#   2. An explicit tier pick (config.whisper_tier = "small" /
#      "turbo", env WHISPER_TIER) wins next.
#   3. Auto: a machine with >=16 GB RAM is offered the turbo
#      tier (large-v3-turbo — near-large accuracy at a fraction
#      of large-v3's cost, the accent-ceiling cure); below that
#      the "small" default stands — the 8 GB standard machine
#      is unchanged. An unreadable RAM total also stays small:
#      the bigger model is an offer, never a gamble.
# RAM is read through total_ram_gb() — the same psutil seam the
# brain tiers use, and the seam tests stub.
# The CT2 turbo snapshot faster-whisper itself resolves for
# "large-v3-turbo". NOTE: there is NO Systran/faster-whisper-
# large-v3-turbo repo — Systran never published a turbo snapshot
# (the HF API answers it 401, byte-identical to a nonsense repo;
# verified live 2026-10-23). faster-whisper's own model map
# points at mobiuslabsgmbh's conversion (the repo now lives
# under dropbox-dash; the mobiuslabsgmbh name still resolves).
# Bundled layout: the small snapshot stages at <assets>/whisper/
# (the pre-A10 layout, unchanged); the turbo snapshot stages at
# <assets>/whisper-turbo/ so the two never overwrite each other.

# Whisper hash pins (v5.47, programme item 17 / C6b): the ears were
# the last unpinned download — faster-whisper fetches its own
# weights, so the _download_once gate never sees the bytes, and
# readiness was a size floor alone. Each pin below was computed
# with sha256sum from the REAL model.bin as downloaded from the
# repo whisper_repo_for() names, on 2026-10-23 (the same
# measure-the-genuine-bytes provenance as DOWNLOAD_SHA256 — the
# "small" pin is Systran/faster-whisper-small's model.bin, the
# turbo pin is mobiuslabsgmbh/faster-whisper-large-v3-turbo's).
# Verification happens at PROVISIONING time in
# _fetch_whisper_weights via verify_whisper_snapshot() below —
# the download-completion gate for a self-managed download —
# NOT inside whisper_ready()/whisper_snapshot_path(): those are
# called on hot paths and, per the v4.2 rule quoted in
# _download_once, files already on disk are not re-hashed on
# every lookup.


# --- Structured pack progress (v3.2) -------------------------------------
# The text progress lines stay exactly as they were (the Brain card
# shows them; the download flow keys off their "ERROR:"/"paused"
# wording), but a percentage SCRAPED out of those lines is how the
# lite session once announced "about 100% downloaded" for a brain
# that had not landed — the last line's % was one file's, not the
# pack's. Alongside the lines, every download now records structured
# byte counts here, and pack_progress() is the single truthful
# snapshot the setup banner, the onboarding setup page, the lite
# session and the doctor all read.
