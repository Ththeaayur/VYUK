# Skills split Phase A batch A3 INTENTIONAL: house helpers shared by
# the inspect group (see core/skills_files3_inspect.py); moved verbatim
# from core/skills_files_inspect.py.

import hashlib
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HASH_CHUNK = 1024 * 1024  # 1 MiB — the app's streamed-hashing chunk


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
    """Return (Image, None) or (None, error string) — Pillow is a house dep."""
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


def _sha256_file(path: Path) -> str:
    """SHA-256 of one file, streamed in 1 MiB chunks (any file size)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()
