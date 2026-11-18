# Skills split (Phase A): shared house helpers moved verbatim from core/skills_files_ops.py.
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


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


def _load_pypdf():
    """Return (module, None) or (None, error string) — pypdf is optional."""
    try:
        import pypdf

        return pypdf, None
    except ImportError:
        return None, ("PDF work isn't available in this build of VYUK — "
                      "the PDF part didn't make it into the package.")


def _load_pil():
    """Return (Image, None) or (None, error string) — Pillow is a house dep."""
    try:
        from PIL import Image

        return Image, None
    except ImportError:
        return None, ("Image work isn't available in this build of VYUK — the "
                      "image part didn't make it into the package.")
