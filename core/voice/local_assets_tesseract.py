"""Tesseract OCR engine family of core/voice/local_assets.py (skills split, batch 2)."""
import os
import shutil
from pathlib import Path
from ..config import DATA_DIR, config  # Phase 3 V2: config stays flat
# INTENTIONAL: batch-2 cross-module import (moved family reads local_assets_model).
from .local_assets_model import (
    _bundled_child,
)


def tesseract_path() -> Path | None:
    """The RESOLVED Tesseract binary, or None when no OCR
    engine exists on this machine."""
    bases = []
    bundled = _bundled_child("ocr")
    if bundled is not None:
        bases.append(bundled)
    bases.append(DATA_DIR / "ocr")
    for base in bases:
        for name in ("tesseract.exe", "tesseract"):
            cand = base / name
            if cand.exists():
                return cand
    found = shutil.which("tesseract")
    return Path(found) if found else None


def configure_pytesseract(pytesseract_module) -> bool:
    """Point pytesseract at the resolved engine. Returns
    True when OCR is usable on this machine."""
    path = tesseract_path()
    if path is None:
        return False
    try:
        pytesseract_module.pytesseract.tesseract_cmd = str(path)
    except Exception:  # noqa: BLE001 — a default PATH lookup may still work
        pass
    # v5.44 (E2): also publish the resolved tessdata directory
    # through TESSDATA_PREFIX, the environment seam the Tesseract
    # subprocess inherits — so even a caller that passes no
    # --tessdata-dir in its config (there is one: the frame OCR in
    # core/voice/local_client.py, which this wave may not touch) finds
    # the bundled languages instead of only the engine's own.
    try:
        tdir = tessdata_dir()
        if tdir is not None:
            os.environ["TESSDATA_PREFIX"] = str(tdir)
    except Exception:  # noqa: BLE001 — env is a fallback seam only
        pass
    return True


OCR_TESSERACT_CONFIG = "--psm 6 --oem 1"


"""The explicit Tesseract config every screen-OCR call passes
(v5.44, E2): page-segmentation mode 6 (a uniform block of text —
the right assumption for a window's worth of UI text) and the
LSTM engine only (oem 1). Before E2 the calls passed NO config,
so Tesseract guessed both, per call, from defaults."""


_HIN_MISSING_NOTE = (
    "Hindi OCR data (hin.traineddata) isn't installed on this "
    "machine, so the screen was read in English only.")


def _tessdata_candidate_dirs() -> list[Path]:
    """Directories that may hold *.traineddata, in resolution
    order: the bundled install copy's tessdata (assets/ocr/
    tessdata, then assets/ocr itself), the provisioned copy
    (data/ocr/tessdata, then data/ocr), then the layouts a PATH
    engine uses (tessdata beside the binary, or share/tessdata).
    Mirrors tesseract_path()'s bundled → data → PATH order."""
    dirs: list[Path] = []
    bundled = _bundled_child("ocr")
    if bundled is not None:
        dirs.append(Path(bundled) / "tessdata")
        dirs.append(Path(bundled))
    data_ocr = DATA_DIR / "ocr"
    dirs.append(data_ocr / "tessdata")
    dirs.append(data_ocr)
    try:
        exe = tesseract_path()
    except Exception:  # noqa: BLE001 — candidates are best-effort
        exe = None
    if exe is not None:
        dirs.append(Path(exe).parent / "tessdata")
        dirs.append(Path(exe).parent.parent / "share" / "tessdata")
    seen: set[Path] = set()
    unique: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def _dir_languages(directory: Path) -> frozenset:
    """The language codes a directory's *.traineddata files offer
    (empty when the directory is absent or holds none)."""
    try:
        if not directory.is_dir():
            return frozenset()
        return frozenset(
            p.name[: -len(".traineddata")]
            for p in directory.glob("*.traineddata"))
    except Exception:  # noqa: BLE001
        return frozenset()


def tessdata_dir() -> Path | None:
    """The RESOLVED tessdata directory: the first candidate that
    holds eng.traineddata (the engine's own language anchors the
    choice — a --tessdata-dir pointing anywhere else would lose
    English), else the first candidate holding any traineddata,
    else None."""
    fallback: Path | None = None
    for d in _tessdata_candidate_dirs():
        langs = _dir_languages(d)
        if "eng" in langs:
            return d
        if langs and fallback is None:
            fallback = d
    return fallback


def tessdata_languages() -> frozenset:
    """Language codes available in the resolved tessdata dir."""
    tdir = tessdata_dir()
    return _dir_languages(tdir) if tdir is not None else frozenset()


def resolve_ocr_language(preferred_language: str | None = None):
    """(lang, tessdata_dir, note): the Tesseract language string
    for one OCR call (v5.44, E2).

    The language follows the user's preferred language
    (config.preferred_language when none is passed): "hi" reads
    Hindi + English when the bundled hin data is present
    ("hin+eng", or "hin" alone when only Hindi data resolves);
    every other preference reads English. A Hindi preference
    whose hin data is MISSING never hard-fails: it degrades to
    "eng" and `note` says so, in words the caller surfaces —
    silent English-only reading of a Hindi screen is the pre-E2
    lie this item removes. `tessdata_dir` is a directory that
    actually contains every language in `lang` (else None, and
    the engine's own default tessdata applies)."""
    if preferred_language is None:
        try:
            preferred_language = getattr(
                config, "preferred_language", "en-gb")
        except Exception:  # noqa: BLE001
            preferred_language = "en-gb"
    pref = str(preferred_language or "").strip().lower()
    by_dir = [(d, _dir_languages(d))
              for d in _tessdata_candidate_dirs()]

    def first_with(*langs: str) -> Path | None:
        for d, have in by_dir:
            if all(lang in have for lang in langs):
                return d
        return None

    if pref == "hi" or pref.startswith("hi-") or pref == "hin":
        both = first_with("hin", "eng")
        if both is not None:
            return "hin+eng", both, None
        hin_only = first_with("hin")
        if hin_only is not None:
            return "hin", hin_only, None
        return "eng", first_with("eng"), _HIN_MISSING_NOTE
    return "eng", first_with("eng"), None


def ocr_tesseract_config(preferred_language: str | None = None):
    """(config, lang, note) for one pytesseract call (v5.44, E2):
    the explicit `--psm 6 --oem 1` config, plus `--tessdata-dir`
    naming the resolved directory whenever it can be passed
    safely (a path containing a space would be split apart by
    pytesseract's command-line assembly — configure_pytesseract's
    TESSDATA_PREFIX covers that install instead, and the config
    then deliberately omits the flag rather than corrupting the
    call)."""
    lang, tdir, note = resolve_ocr_language(preferred_language)
    config = OCR_TESSERACT_CONFIG
    if tdir is not None and " " not in str(tdir):
        config = f"{config} --tessdata-dir {tdir}"
    return config, lang, note
