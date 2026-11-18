# Photo editing house helpers (shared by the edit + finish tools).
# Split out of core/skills_photo.py (skills split Phase A batch A17).

import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
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


def _load_pil():
    """Return (Image, None) or (None, error string) — Pillow is a house dep."""
    try:
        from PIL import Image

        return Image, None
    except ImportError:
        return None, ("Image work isn't available in this build of VYUK — the "
                      "image part didn't make it into the package.")


def _load_oriented(path):
    """Load one image with its EXIF orientation baked in.

    THE shared loader for every tool in this pack: validate the
    path exists, open the file, fully load it, then apply
    ImageOps.exif_transpose so all geometry from here on is the
    displayed geometry (the v4.7 rule).
    Returns (image, target, None) or (None, None, error string).
    """
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return None, None, target
    Image, err = _load_pil()
    if err:
        return None, None, err
    try:
        image = Image.open(target)
        image.load()
    except Exception as e:  # noqa: BLE001
        return None, None, (f"Could not read '{target.name}' as an image: {e}. "
                            "The file may be corrupt or in a format Pillow "
                            "doesn't support.")
    try:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
    except Exception:  # noqa: BLE001 — no usable EXIF: work with stored pixels
        pass
    return image, target, None


def _has_alpha(image) -> bool:
    """True when the image carries a real alpha channel."""
    if image.mode in ("RGBA", "LA"):
        return True
    if image.mode == "P" and "transparency" in image.info:
        return True
    return False


def _flatten_on_white(Image, image):
    """Composite an image over an opaque white background → RGB."""
    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(background, rgba).convert("RGB")


def _resolve_output(target: Path, output: str, default_name: str) -> Path:
    """The caller's output path, or `default_name` beside the input."""
    if output and str(output).strip():
        return Path(str(output)).expanduser()
    return target.with_name(default_name)


def _save_result(Image, image, dest: Path, fallback_format: str | None):
    """Save `image` to `dest` without ever overwriting.

    JPEG/BMP can't hold transparency: an image with alpha is
    flattened onto WHITE first and the returned notes say so (the
    convert_image behaviour). A name that is taken gets a
    " (2)"-style suffix, also noted. Returns (final_path, notes)
    or (None, error string); a half-written file is removed.
    """
    notes: list[str] = []
    final = _non_clobbering(dest)
    if final != dest:
        notes.append(f"'{dest.name}' already existed, so the suffixed "
                     f"name '{final.name}' was used.")
    pil_format = fallback_format
    if final.suffix:
        try:
            Image.init()
            pil_format = Image.EXTENSION.get(final.suffix.lower(), pil_format)
        except Exception:  # noqa: BLE001 — fall back to extension-less save
            pil_format = fallback_format
    to_save = image
    if pil_format in ("JPEG", "BMP"):
        if _has_alpha(to_save):
            to_save = _flatten_on_white(Image, to_save)
            notes.append(f"{pil_format} can't hold transparency, so the "
                         "image was flattened onto white.")
        elif to_save.mode not in ("RGB", "L", "CMYK"):
            to_save = to_save.convert("RGB")
    try:
        if final.suffix:
            to_save.save(final)
        else:  # no extension to infer from — keep the source format
            to_save.save(final, format=pil_format or "PNG")
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()  # never leave a half-written image behind
        except OSError:
            pass
        return None, f"The edited image couldn't be written: {e}"
    return final, notes


def _result_line(verb: str, target: Path, old_size, new_size,
                 final: Path, notes: list[str]) -> str:
    lines = [
        f"{verb} '{target.name}': {old_size[0]}×{old_size[1]} → "
        f"{new_size[0]}×{new_size[1]} pixels, saved to {final} "
        f"({_human_size(final.stat().st_size)})."
    ]
    lines.extend(notes)
    return " ".join(lines)


def _as_int(value, name: str):
    """Coerce to int, or return an error string naming the field."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return f"{name} must be a whole number (got {value!r})."


def _as_float(value, name: str):
    """Coerce to a finite float, or return an error string."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return f"{name} must be a number (got {value!r})."
    if not math.isfinite(result):
        return f"{name} must be a finite number (got {value!r})."
    return result


def _rgb_ready(image):
    """Palette/CMYK images → RGB(A): enhancement and filter maths
    must run on real colour bands, not palette indexes."""
    if image.mode == "P":
        return image.convert("RGBA" if _has_alpha(image) else "RGB")
    if image.mode == "CMYK":
        return image.convert("RGB")
    return image
