"""Shared house helpers for the skills_files_media_ops pack (moved verbatim)."""

from pathlib import Path

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
               ".tif", ".tiff")
_MAP_ENTRY_CAP = 20000      # files counted in a size map before we stop
_MAP_LIST_CAP = 15          # entries shown in the map (the tool's spec)
_PDF_IMAGE_LIST_CAP = 20    # extracted names shown before disclosure

# ---------------------------------------------------------------------------
# Helpers — the house set, re-factored locally per pack (the
# skills_files_inspect pattern) so no pack imports another's privates.
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
    """Return (PIL module pieces, None) or (None, error string)."""
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


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fmt_rgb(rgb) -> str:
    return f"({rgb[0]:.0f}, {rgb[1]:.0f}, {rgb[2]:.0f})"
