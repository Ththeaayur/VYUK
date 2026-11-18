# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: moved verbatim from
# core/skills_media.py (see that facade). Imports travel with the unit
# (plan SS2 P3); cross-part names imported with INTENTIONAL comments.

# INTENTIONAL (media split): shared names live in skills_media_helpers.
import io
from .skills_media_helpers import (
    _IMAGE_FORMATS,
    _apply_exif_orientation,
    _existing_file,
    _human_size,
    _load_pil,
    _truncate,
)


def convert_image(path: str, out_format: str) -> str:
    """Convert an image to png/jpg/webp/bmp, saved beside the original."""
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return target
    fmt_key = (out_format or "").strip().lower().lstrip(".")
    if fmt_key not in _IMAGE_FORMATS:
        return (f"Unknown output format: '{out_format}'. "
                "Use one of: png, jpg, webp, bmp.")
    Image, err = _load_pil()
    if err:
        return err
    try:
        with Image.open(target) as image:
            image.load()
            image = _apply_exif_orientation(image)
            dest = target.with_suffix("." + ("jpg" if fmt_key == "jpeg" else fmt_key))
            if dest.resolve() == target.resolve():
                # Converting to the format it already is — never overwrite
                # the original; write a clearly-named copy instead.
                dest = target.with_name(target.stem + "_converted" + dest.suffix)
            to_save = image
            if _IMAGE_FORMATS[fmt_key] in ("JPEG", "BMP") and image.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                mask = image.convert("RGBA").split()[-1] if image.mode != "P" else None
                background.paste(image.convert("RGBA"), mask=mask)
                to_save = background
            elif _IMAGE_FORMATS[fmt_key] == "JPEG" and image.mode != "RGB":
                to_save = image.convert("RGB")
            to_save.save(dest, _IMAGE_FORMATS[fmt_key])
    except Exception as e:  # noqa: BLE001
        return (f"Could not convert '{target.name}': {e}. "
                "The file may not be an image, or the format is unsupported.")
    return (f"Converted '{target.name}' to {fmt_key.upper()}: {dest} "
            f"({_human_size(target.stat().st_size)} → {_human_size(dest.stat().st_size)}).")


def resize_image(path: str, width: int = 0, height: int = 0, keep_aspect: bool = True) -> str:
    """Resize an image, saving '<name>_resized.<ext>' beside the original."""
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return target
    try:
        width = int(width) if width else 0
        height = int(height) if height else 0
    except (TypeError, ValueError):
        return "Width and height must be whole numbers of pixels."
    if width <= 0 and height <= 0:
        return "Resizing needs a target width and/or height in pixels."
    for label, value in (("width", width), ("height", height)):
        if value and not (1 <= value <= 20000):
            return f"That {label} ({value}) is outside the sane range of 1–20000 pixels."
    Image, err = _load_pil()
    if err:
        return err
    try:
        with Image.open(target) as image:
            image.load()
            orig_format = image.format  # transposed copies lose .format
            # Display orientation first: the reported and computed
            # dimensions are the ones the user actually sees.
            image = _apply_exif_orientation(image)
            orig_w, orig_h = image.size
            if keep_aspect:
                if width and height:
                    scale = min(width / orig_w, height / orig_h)
                elif width:
                    scale = width / orig_w
                else:
                    scale = height / orig_h
                new_size = (max(1, round(orig_w * scale)), max(1, round(orig_h * scale)))
            else:
                if not width or not height:
                    return ("Exact resizing (keep_aspect=false) needs BOTH a width "
                            "and a height — one dimension can't be stretched alone.")
                new_size = (width, height)
            resized = image.resize(new_size, Image.LANCZOS)
            dest = target.with_name(target.stem + "_resized" + target.suffix)
            save_kwargs = {}
            if orig_format:
                save_kwargs["format"] = orig_format
            resized.save(dest, **save_kwargs)
    except Exception as e:  # noqa: BLE001
        return (f"Could not resize '{target.name}': {e}. "
                "The file may not be an image, or the format is unsupported.")
    return (f"Resized '{target.name}' from {orig_w}×{orig_h} to "
            f"{new_size[0]}×{new_size[1]}: {dest} ({_human_size(dest.stat().st_size)}).")


def compress_image(path: str, quality: int = 70, target_kb: int = 0) -> str:
    """Re-encode an image as a quality-tuned JPEG: '<name>_compressed.jpg'.

    target_kb > 0 asks for a SIZE instead of a quality: the encode
    starts at `quality` and steps down (never below 5) until the
    file fits the budget; when even quality 5 misses, the smallest
    encode is kept and the result says so honestly."""
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return target
    try:
        quality = int(quality)
    except (TypeError, ValueError):
        return "Quality must be a number between 5 and 95."
    quality = max(5, min(95, quality))
    try:
        budget_kb = int(target_kb or 0)
    except (TypeError, ValueError):
        return "target_kb must be a whole number of kilobytes (0 = no target)."
    if budget_kb < 0:
        return "target_kb can't be negative — use 0 for no size target."
    Image, err = _load_pil()
    if err:
        return err
    used_quality = quality
    budget_note = ""
    try:
        with Image.open(target) as image:
            image.load()
            image = _apply_exif_orientation(image)
            rgb = image.convert("RGB") if image.mode != "RGB" else image
            dest = target.with_name(target.stem + "_compressed.jpg")
            if budget_kb:
                budget = budget_kb * 1024
                blob = None
                q = quality
                while True:
                    buffer = io.BytesIO()
                    rgb.save(buffer, "JPEG", quality=q, optimize=True)
                    blob = buffer.getvalue()
                    if len(blob) <= budget or q <= 5:
                        break
                    q = max(5, q - 10)
                used_quality = q
                dest.write_bytes(blob)
                if len(blob) <= budget:
                    budget_note = (f" Target was at most {budget_kb} KB "
                                   "— met.")
                else:
                    budget_note = (f" Target was at most {budget_kb} KB "
                                   "— even quality 5 couldn't get under "
                                   "it, so this is the smallest encode.")
            else:
                rgb.save(dest, "JPEG", quality=quality, optimize=True)
    except Exception as e:  # noqa: BLE001
        return (f"Could not compress '{target.name}': {e}. "
                "The file may not be an image, or the format is unsupported.")
    before = target.stat().st_size
    after = dest.stat().st_size
    change = (1 - after / before) * 100 if before else 0.0
    if after < before:
        verdict = f"{change:.0f}% smaller"
    elif after == before:
        verdict = "exactly the same size — it was already tightly compressed"
    else:
        verdict = (f"{abs(change):.0f}% LARGER — the original was already compressed "
                   "harder than this quality setting")
    return (f"Compressed '{target.name}' at quality {used_quality}: {dest} "
            f"({_human_size(before)} → {_human_size(after)}, {verdict})."
            f"{budget_note}")


def ocr_file(path: str) -> str:
    """Extract the text from an image FILE with Tesseract OCR, if installed."""
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return target
    try:
        import pytesseract
    except ImportError:
        return "Text reading isn't available in this build of VYUK — the OCR part didn't make it into the package."
    except Exception as e:  # noqa: BLE001
        return f"Text reading couldn't start: the OCR library failed to load ({e})."
    from .voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/

    if not local_assets.configure_pytesseract(pytesseract):
        return "Text reading isn't set up on this machine yet — the OCR engine that comes with VYUK isn't in place. It arrives with the installer; nothing for you to download."
    Image, err = _load_pil()
    if err:
        return err
    try:
        import pytesseract

        with Image.open(target) as image:
            text = pytesseract.image_to_string(image).strip()
    except Exception as e:  # noqa: BLE001
        return f"OCR failed: {e}"
    if not text:
        return f"OCR ran on '{target.name}', but no readable text was found in it."
    return f"Text in '{target.name}':\n" + _truncate(text, 1500)


# SKILLS SPLIT PHASE A BATCH A21 INTENTIONAL: handlers keep their public
# module path (core.skills_media) so the override allow-list in
# core/funnel/tools_registry.py — which matches on handler.__module__ —
# keeps working with NO change to that file. The facade is the module's
# public identity; the impl modules are its private home.
convert_image.__module__ = "core.skills_media"
resize_image.__module__ = "core.skills_media"
compress_image.__module__ = "core.skills_media"
ocr_file.__module__ = "core.skills_media"
