import re
from pathlib import Path


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
               ".tif", ".tiff")
_COOK_SCAN_CAP = 500        # files a recipe will touch in one run
_SHEET_CAP = 30             # images on one contact sheet
_SPLIT_PART_CAP = 50        # parts one split will write
_PHOTO_SCAN_CAP = 1000      # images hashed by the duplicate finder
_AGE_LIST_CAP = 15          # files listed in the age report

# ---------------------------------------------------------------------------
# Helpers — the house set, re-factored locally per pack (the
# skills_files_inspect/files_media_ops pattern) so no pack imports another's privates.
# Library loaders are module-level on purpose: tests monkeypatch
# them to stand in for machines where a library is present/absent.
# ---------------------------------------------------------------------------


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _existing_file(path: str, what: str = "file") -> Path | None | str:
    """Resolve a user path to an existing file, or return an error string."""
    if not path or not str(path).strip():
        return f"That needs the path of a {what}."
    target = Path(str(path)).expanduser()
    if not target.exists():
        return f"File not found: {target}"
    if not target.is_file():
        return f"That's a folder, not a file: {target}"
    return target


def _existing_folder(path: str, what: str = "folder") -> Path | None | str:
    """Resolve a user path to an existing folder, or return an error string."""
    if not path or not str(path).strip():
        return f"That needs the path of a {what}."
    target = Path(str(path)).expanduser()
    if not target.exists():
        return f"Folder not found: {target}"
    if not target.is_dir():
        return f"That's a file, not a folder: {target}"
    return target


def _non_clobbering(dest: Path) -> Path:
    """Return `dest`, or 'stem (2).suffix', 'stem (3).suffix', … —
    the first variant that doesn't exist yet. Never the taken name."""
    if not dest.exists():
        return dest
    counter = 2
    while True:
        candidate = dest.with_name(f"{dest.stem} ({counter}){dest.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _suffix_note(dest: Path, final: Path) -> str:
    """The standard 'name was taken' disclosure, or ''."""
    if final == dest:
        return ""
    return (f" '{dest.name}' already existed, so the suffixed name "
            f"'{final.name}' was used.")


def _load_pil():
    """Return (PIL Image module, None) or (None, error string)."""
    try:
        from PIL import Image

        return Image, None
    except ImportError:
        return None, ("Image work isn't available in this build of VYUK — the "
                      "image part didn't make it into the package.")


def _load_pypdf():
    """Return (module, None) or (None, error string) — pypdf is optional."""
    try:
        import pypdf

        return pypdf, None
    except ImportError:
        return None, ("PDF work isn't available in this build of VYUK — "
                      "the PDF part didn't make it into the package.")


def _load_numpy():
    """Return (numpy module, None) or (None, error string)."""
    try:
        import numpy

        return numpy, None
    except ImportError:
        return None, ("Audio work isn't available in this build of VYUK — the "
                      "audio part didn't make it into the package.")


def _load_cv2():
    """Return (cv2 module, None) or (None, error string) — OpenCV is
    an optional extra (vision.py feature-detects it the same way)."""
    try:
        import cv2

        return cv2, None
    except ImportError:
        return None, ("Face detection needs the optional OpenCV package "
                      "(opencv-python), which isn't installed on this "
                      "machine — so I can't look for faces in the photo.")


def _fmt_seconds(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)} minute(s) {rest:.0f} seconds"


def _list_images(folder: Path) -> list[Path]:
    """Image files directly in a folder, name-sorted; nothing recursed."""
    return sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in _IMAGE_EXTS),
        key=lambda p: p.name.lower())


def _safe_stem(text: str, fallback: str) -> str:
    """A filename-safe stem from free text (marker lines, titles)."""
    cleaned = re.sub(r"[^\w\- ]+", "", str(text or ""), flags=re.UNICODE)
    cleaned = re.sub(r"\s+", "_", cleaned.strip()).strip("_")
    return cleaned[:40] or fallback
