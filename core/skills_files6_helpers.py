"""Shared house helpers for the Files & Media IV pack (skills_files_data).

Moved verbatim from core/skills_files_data.py in the skills split
(Phase A); the helpers module sits at the bottom of the dependency
order: helpers <- impl modules <- facade.
"""

from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers — the house set, re-factored locally per pack (the
# skills_files_inspect/files_media_ops/files_recipes pattern) so no pack imports another's privates.
# ---------------------------------------------------------------------------

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

def _clamp_int(value, low: int, high: int, default: int) -> int:
    """Coerce a tool argument to an int inside [low, high]."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))

def _fmt_num(value: float) -> str:
    """Compact number rendering: whole floats without decimals."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6g}"
