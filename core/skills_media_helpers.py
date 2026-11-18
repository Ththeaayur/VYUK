# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: moved verbatim from
# core/skills_media.py (see that facade). Imports travel with the unit
# (plan SS2 P3); cross-part names imported with INTENTIONAL comments.

import sys
from datetime import datetime
from pathlib import Path


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


_RECORD_RATE = 44100


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated — {len(text)} chars total]"


_NOT_WINDOWS = "this system is not Windows, so nothing was changed."


_IMAGE_FORMATS = {"png": "PNG", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP", "bmp": "BMP"}


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


def _load_pil():
    """Return (Image, None) or (None, error string) — Pillow is a house dep."""
    try:
        from PIL import Image

        return Image, None
    except ImportError:
        return None, ("Image work isn't available in this build of VYUK — the "
                      "image part didn't make it into the package.")


def _apply_exif_orientation(image):
    """Return `image` transposed into its EXIF display orientation.

    Phone photos store their pixels sideways plus an orientation tag
    saying how they should be shown. Working on the stored pixels
    computes geometry for a picture nobody ever sees, and saving
    without the tag then locks the wrong rotation in — so every tool
    that loads and re-saves pixels runs this first: afterwards the
    in-memory image IS the displayed image (dimensions included) and
    the orientation tag is reset, so the saved file displays the
    same way everywhere. Images without a tag pass through with
    their pixels unchanged."""
    try:
        from PIL import ImageOps

        return ImageOps.exif_transpose(image)
    except Exception:  # noqa: BLE001 — orientation must never break a save
        return image


def _save_screenshot(image, kind: str) -> Path:
    """Save a PIL image under data/screenshots/ with a timestamped name
    (the file-saving sibling of control.take_screenshot)."""
    out_dir = _DATA_DIR / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"screenshot_{kind}_{stamp}.png"
    counter = 1
    while dest.exists():
        dest = out_dir / f"screenshot_{kind}_{stamp}_{counter}.png"
        counter += 1
    image.save(dest, "PNG")
    return dest
