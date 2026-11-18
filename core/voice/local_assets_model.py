"""Model asset family (SKILLS SPLIT PHASE B, local_assets batch 1).

Tier/config, bundled/data model paths, and model readiness, split out
of core/voice/local_assets.py. The facade (local_assets.py) re-exports
every name here; behaviour is unchanged.
"""

import os
import sys
from pathlib import Path

from ..config import DATA_DIR, config
from .. import config_spec
from ..model_tiers import MODEL_TIERS

# MODEL_TIERS is the one canonical tier table (GAPS-22, B-V1/B-V2);
# it lives in core/model_tiers.py now — this module imports it, as
# does core/model_manager.py, so the two can never drift apart.
DEFAULT_MODEL_URL = MODEL_TIERS["balanced"]["url"]

MODELS_DIR = DATA_DIR / "models"


def _package_root() -> Path | None:
    """The package root (the folder holding main.py, core/, data/).

    Derived from DATA_DIR — config.py builds DATA_DIR as
    <package root>/data, so its parent IS the package root. Deriving
    it here (rather than importing config.PROJECT_ROOT) keeps this
    module importable against config stand-ins that predate that
    name, and the probe must never raise: bundled resolution sits
    inside readiness checks all over the app, so any surprise here
    degrades to "no bundled pack", never to a broken check."""
    try:
        return Path(DATA_DIR).resolve().parent
    except Exception:  # noqa: BLE001 — fail-safe by design (see above)
        return None


def bundled_assets_dir() -> Path | None:
    """The bundled assets root, or None when this install carries no
    bundled pack (a plain source checkout, the shipped source zip).

    Resolution, first hit wins:
      1. $NOVA_ASSETS_DIR — explicit override (dev staging, tests).
      2. Frozen (PyInstaller) builds: <exe_dir>/assets — the layout
         packaging/nova.iss installs ({app}\\VYUK.exe + {app}\\assets).
      3. Dev staging: an assets/ folder inside the package root, or
         beside it (the project-root layout the packaging scripts
         stage into).
    A candidate only counts when it is an existing directory; a set
    but missing env var is ignored rather than fatal."""
    env = (config_spec.explicit("NOVA_ASSETS_DIR") or "").strip()
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_dir():
            return candidate
    if getattr(sys, "frozen", False):
        candidate = Path(sys.executable).resolve().parent / "assets"
        if candidate.is_dir():
            return candidate
    package_root = _package_root()
    if package_root is not None:
        for base in (package_root, package_root.parent):
            candidate = base / "assets"
            if candidate.is_dir():
                return candidate
    return None


def _bundled_child(*parts: str) -> Path | None:
    """Path inside the bundled root, or None when there is no root."""
    root = bundled_assets_dir()
    return root.joinpath(*parts) if root is not None else None


def model_tier_for(filename: str) -> tuple[str, dict] | None:
    """Returns (tier_name, tier_dict) when `filename` is one of the
    auto-downloadable tier models, else None (a custom model)."""
    for tier_name, tier in MODEL_TIERS.items():
        if tier["file"] == filename:
            return (tier_name, tier)
    return None


def model_url_for(filename: str) -> str | None:
    """The download URL for a tier filename, or None for custom files
    (which are never auto-downloaded — the user places them by hand)."""
    found = model_tier_for(filename)
    return found[1]["url"] if found else None


def total_ram_gb() -> float | None:
    """Total physical RAM in GB via psutil, or None when unknown
    (psutil missing or the total unreadable). Never raises."""
    try:
        import psutil

        return psutil.virtual_memory().total / 1_000_000_000
    except Exception:  # noqa: BLE001 — sizing must never break setup
        return None


def auto_tier_for_ram(ram_gb: float | None) -> str:
    """The RAM-sized default tier (pure; rules in the section header)."""
    if ram_gb is None:
        return "balanced"
    if ram_gb <= 10:
        return "lite"
    if ram_gb <= 20:
        return "balanced"
    return "quality"


def explicit_model_file() -> str | None:
    """The LOCAL_MODEL_FILE the user explicitly set (environment or
    .env), or None when nothing was set and the config value is just
    the built-in default — the distinction that makes a file "their
    pick" rather than "the default"."""
    value = (config_spec.explicit("LOCAL_MODEL_FILE") or "").strip()
    return value or None


def _existing_tier_file() -> str | None:
    """A tier GGUF already fully downloaded in data/models, if any:
    the configured file first, then the biggest tier present.

    GAPS-22 (B-V1): the scan goes through
    ``model_manager.resolve_model`` now — the one resolution, over
    real discovery with the readiness floor — instead of a second
    hand-rolled candidate loop. Behaviour is preserved exactly,
    including the tier-only rule: a configured file that is not a
    tier file is not preferred here (the explicit pick is
    ``effective_model()``'s own first branch, above) — a
    configured custom file still falls through to the biggest
    tier present, as before.
    """
    from ..model_manager import discover_models, resolve_model

    configured = (config.local_model_file or "").strip() or None
    if configured is not None and model_tier_for(configured) is None:
        configured = None
    resolved = resolve_model(discover_models(DATA_DIR),
                             preferred_file=configured,
                             min_bytes=MIN_MODEL_BYTES)
    return resolved["file"] if resolved["status"] == "ok" else None


def effective_model() -> dict:
    """The model the built-in brain will use, and why.

    Returns {"file", "tier" (tier name, or None for a custom file),
    "label", "source" ("your pick" | "already downloaded" | "auto"),
    "ram_gb" (float or None)}. Pure reads — safe to call from the
    doctor, the GUI and tests."""
    ram_gb = total_ram_gb()
    picked = explicit_model_file()
    if picked:
        found = model_tier_for(picked)
        return {
            "file": picked,
            "tier": found[0] if found else None,
            "label": found[1]["label"] if found else picked,
            "source": "your pick",
            "ram_gb": ram_gb,
        }
    existing = _existing_tier_file()
    if existing:
        tier_name, tier = model_tier_for(existing)
        return {
            "file": existing,
            "tier": tier_name,
            "label": tier["label"],
            "source": "already downloaded",
            "ram_gb": ram_gb,
        }
    tier_name = auto_tier_for_ram(ram_gb)
    tier = MODEL_TIERS[tier_name]
    return {
        "file": tier["file"],
        "tier": tier_name,
        "label": tier["label"],
        "source": "auto",
        "ram_gb": ram_gb,
    }


def effective_model_file() -> str:
    """The filename of effective_model() — the one name the download,
    the readiness checks and the llama load all agree on."""
    return effective_model()["file"]


def explicit_n_ctx() -> int | None:
    """The LLM_N_CTX the user explicitly set (environment or .env —
    config.py loads .env into os.environ, so one probe covers
    both), parsed as a positive int, or None when unset or
    unreadable. Mirrors explicit_model_file(): a hand-set value is
    the user's own tuning and always wins over the RAM rules."""
    raw = (config_spec.explicit("LLM_N_CTX") or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def effective_n_ctx() -> int:
    """The llama context window the built-in brain runs with.

    Resolution: an explicit LLM_N_CTX wins; a PC with ≤10 GB RAM
    gets 2048; otherwise 4096 (the v3.2 default — the value
    core/voice/local_client.py's LLM_N_CTX names)."""
    picked = explicit_n_ctx()
    if picked is not None:
        return picked
    ram_gb = total_ram_gb()
    if ram_gb is not None and ram_gb <= 10:
        return 2048
    return 4096


def effective_idle_unload_s() -> float:
    """Seconds of quiet before the idle unload drops the brain and
    the ears: 300 s on a ≤10 GB PC, else 600 s (the default
    core/voice/local_client.py's IDLE_UNLOAD_S names)."""
    ram_gb = total_ram_gb()
    if ram_gb is not None and ram_gb <= 10:
        return 300.0
    return 600.0


MIN_MODEL_BYTES = 1_000_000_000          # sanity floor: real tier GGUFs are 1.1 / 2.5 / 5.0 GB


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def bundled_model_path() -> Path | None:
    """The bundled brain GGUF, when the install carries one: any tier
    file in the bundled models dir that clears the readiness floor —
    the same resolution ``_existing_tier_file`` runs over
    data/models, through ``model_manager.resolve_model`` (GAPS-22,
    B-V1: one resolution, not a second candidate loop). None when
    there is no bundled root or no usable GGUF."""
    models = _bundled_child("models")
    if models is None or not models.is_dir():
        return None
    from ..model_manager import discover_models, resolve_model

    configured = (config.local_model_file or "").strip() or None
    if configured is not None and model_tier_for(configured) is None:
        configured = None
    resolved = resolve_model(discover_models(models.parent),
                             preferred_file=configured,
                             min_bytes=MIN_MODEL_BYTES)
    if resolved["status"] != "ok":
        return None
    return models / resolved["file"]


def model_path() -> Path:
    """Where the built-in LLM file lives — the RESOLVED path every
    consumer (model_ready(), the llama load, the doctor) reads.

    v4.0: a bundled GGUF (shipped inside the install) wins — it is
    read-only install content, verified at build time. Otherwise the
    EFFECTIVE model (v3.2 — the explicit pick, else a tier already on
    disk, else the RAM-sized tier; see effective_model()) under
    data/models, exactly as before."""
    bundled = bundled_model_path()
    if bundled is not None:
        return bundled
    return MODELS_DIR / effective_model_file()


def download_model_path() -> Path:
    """Where a brain (re)download lands: ALWAYS data/models. The
    install tree is never written by the app — the bundled copy is
    the floor a fresh install starts from, data/ is the repair and
    upgrade path on top of it."""
    return MODELS_DIR / effective_model_file()


def model_is_bundled() -> bool:
    """True when the resolved brain (model_path()) is the bundled
    install copy — the doctor's "(bundled)" note reads this."""
    bundled = bundled_model_path()
    return bundled is not None and model_path() == bundled


def model_ready() -> bool:
    return _size(model_path()) > MIN_MODEL_BYTES


def brain_ready() -> bool:
    """The brain piece of the pack: the configured GGUF is on disk."""
    return model_ready()
