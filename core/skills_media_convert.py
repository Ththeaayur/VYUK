"""
Media II skills pack for VYUK (v5.11) — the media jobs the earlier
packs never covered: resizing an image, converting an image to a
different format, and splitting a PDF into separate files by page
range.

Same rules as core/skills_files_ops.py and core/skills_media.py: plain
functions returning human-readable strings, never raising;
platform-specific dependencies (PIL, pypdf) are imported lazily
inside the function that needs them and guarded, so this module
imports cleanly on any OS. Nothing here ever overwrites silently:
an output name that is taken gets a " (2)"-style suffix and the
result reports the real name. Inputs are validated — read,
measured, page-counted — BEFORE anything is written, so a bad
input aborts cleanly with no partial output left behind.

Image geometry always follows the EXIF orientation (the v4.7
rule): a photo is oriented first, then measured and resized, so
the dimensions in every result are the ones the picture is
actually displayed at.

Wiring: the parent registers this pack via register_all(registry)
— this module never touches the registry singleton itself.
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_SIDE = 20000  # no output image may exceed this on either side


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


def _load_pypdf():
    """Return (module, None) or (None, error string) — pypdf is optional."""
    try:
        import pypdf

        return pypdf, None
    except ImportError:
        return None, ("PDF work isn't available in this build of VYUK — "
                      "the PDF part didn't make it into the package.")


def _lanczos(Image):
    """The LANCZOS resampling filter, across Pillow generations."""
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return resampling.LANCZOS
    return Image.LANCZOS


def _suffix_note(dest: Path, final: Path) -> str:
    """The standard 'name was taken' disclosure, or ''."""
    if final == dest:
        return ""
    return (f" '{dest.name}' already existed, so the suffixed name "
            f"'{final.name}' was used.")


def _open_oriented(Image, target: Path):
    """Open an image and bake in its EXIF orientation.

    Returns (image, None) with the image fully loaded and oriented
    (displayed geometry), or (None, error string). The image is
    detached from the file handle before returning."""
    try:
        image = Image.open(target)
        image.load()
    except Exception as e:  # noqa: BLE001
        return None, (f"Could not read '{target.name}' as an image: {e}. "
                      "The file may be corrupt or in a format Pillow "
                      "doesn't support.")
    try:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
    except Exception:  # noqa: BLE001 — no usable EXIF: work with stored pixels
        pass
    return image, None


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


# ---------------------------------------------------------------------------
# RESIZE IMAGE
# ---------------------------------------------------------------------------


def resize_image(path: str, width: int = 0, height: int = 0,
                 scale_percent: int = 0, output: str = "") -> str:
    """Resize one image, keeping its format.

    Exactly one sizing mode applies: width only or height only
    (the other side follows the aspect ratio), width AND height
    (exact — the picture is stretched to fit, and the result says
    so when its proportions change), or scale_percent (1–1000).
    The EXIF orientation is applied BEFORE measuring, so all sizes
    are displayed sizes. The input is never overwritten: the
    default output is '<name>_resized.<ext>' beside it, and a
    taken output name gets a ' (2)'-style suffix.
    """
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return target
    try:
        width = int(width or 0)
        height = int(height or 0)
        scale_percent = int(scale_percent or 0)
    except (TypeError, ValueError):
        return "Width, height and scale_percent must be whole numbers."
    if width < 0 or height < 0 or scale_percent < 0:
        return "Sizes can't be negative — give a width, a height, or a scale_percent."
    if scale_percent and (width or height):
        return ("Pick ONE sizing mode: a width, a height, both, or a "
                "scale_percent — not a scale and a size together.")
    if not (width or height or scale_percent):
        return ("Nothing to resize to — give a width, a height, both, "
                "or a scale_percent (e.g. 50 to halve the picture).")
    if scale_percent and not 1 <= scale_percent <= 1000:
        return ("scale_percent must be between 1 and 1000 "
                f"(got {scale_percent}).")
    if width > _MAX_SIDE or height > _MAX_SIDE:
        return (f"That target is too large — no side may exceed "
                f"{_MAX_SIDE} pixels (got {width}×{height}).")
    Image, err = _load_pil()
    if err:
        return err
    image, err = _open_oriented(Image, target)
    if err:
        return err
    old_w, old_h = image.size
    if width and height:
        new_w, new_h = width, height
    elif width:
        new_w = width
        new_h = max(1, round(old_h * width / old_w))
    elif height:
        new_w = max(1, round(old_w * height / old_h))
        new_h = height
    else:
        new_w = max(1, round(old_w * scale_percent / 100))
        new_h = max(1, round(old_h * scale_percent / 100))
    if new_w > _MAX_SIDE or new_h > _MAX_SIDE:
        return (f"That would make the picture {new_w}×{new_h} — over the "
                f"{_MAX_SIDE}-pixel-per-side limit, so nothing was written.")
    try:
        resized = image.resize((new_w, new_h), _lanczos(Image))
    except Exception as e:  # noqa: BLE001
        return f"Could not resize '{target.name}': {e}"
    if output and str(output).strip():
        dest = Path(str(output)).expanduser()
    else:
        dest = target.with_name(f"{target.stem}_resized{target.suffix}")
    final = _non_clobbering(dest)
    try:
        if final.suffix:
            resized.save(final)
        else:  # no extension to infer from — keep the source format
            resized.save(final, format=image.format or "PNG")
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()  # never leave a half-written image behind
        except OSError:
            pass
        return f"The resized image couldn't be written: {e}"
    lines = [
        f"Resized '{target.name}': {old_w}×{old_h} → {new_w}×{new_h} pixels, "
        f"saved to {final} ({_human_size(final.stat().st_size)})."
    ]
    if width and height and (new_w * old_h != new_h * old_w):
        lines.append(
            "Both a width and a height were given, so the picture was "
            "stretched to exactly that size — its proportions changed.")
    note = _suffix_note(dest, final)
    if note:
        lines.append(note.strip())
    return " ".join(lines)


# ---------------------------------------------------------------------------
# CONVERT IMAGE
# ---------------------------------------------------------------------------

_FORMATS = {
    "png": ("PNG", ".png"),
    "jpg": ("JPEG", ".jpg"),
    "webp": ("WEBP", ".webp"),
    "bmp": ("BMP", ".bmp"),
}


def convert_image(path: str, format: str, output: str = "",
                  quality: int = 0) -> str:
    """Convert one image to png, jpg, webp or bmp.

    (The parameter is named `format` because that's the tool's
    schema; it shadows the builtin inside this function only.)

    Format traps are handled, and disclosed: JPEG (and BMP) can't
    hold transparency, so an image with alpha is flattened onto
    WHITE first and the result says so; palette and CMYK images
    are converted to RGB where the target needs it. The default
    output keeps the same name with the new extension, beside the
    input; a taken name gets a ' (2)'-style suffix — the input is
    never overwritten. `quality` (1–95) applies to the lossy
    formats (jpg, webp); 0 leaves the encoder at its default.
    """
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return target
    fmt_key = str(format or "").strip().lower().lstrip(".")
    if fmt_key == "jpeg":
        fmt_key = "jpg"
    if fmt_key not in _FORMATS:
        return ("That output format isn't supported — pick one of: "
                "png, jpg, webp, bmp.")
    try:
        q = int(quality or 0)
    except (TypeError, ValueError):
        return "quality must be a whole number between 1 and 95 (0 = encoder default)."
    if q and not 1 <= q <= 95:
        return f"quality must be between 1 and 95 (got {q}); 0 = encoder default."
    pil_format, extension = _FORMATS[fmt_key]
    Image, err = _load_pil()
    if err:
        return err
    image, err = _open_oriented(Image, target)
    if err:
        return err
    notes: list[str] = []
    if pil_format in ("JPEG", "BMP"):
        if _has_alpha(image):
            image = _flatten_on_white(Image, image)
            notes.append(
                f"{pil_format} can't hold transparency, so the image "
                "was flattened onto white.")
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
    elif image.mode == "CMYK":
        image = image.convert("RGB")
    if output and str(output).strip():
        dest = Path(str(output)).expanduser()
    else:
        dest = target.with_name(f"{target.stem}{extension}")
    final = _non_clobbering(dest)
    save_kwargs: dict = {}
    if q and pil_format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = q
    try:
        image.save(final, format=pil_format, **save_kwargs)
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()  # never leave a half-written image behind
        except OSError:
            pass
        return f"'{target.name}' couldn't be converted to {pil_format}: {e}"
    lines = [
        f"Converted '{target.name}' to {pil_format}: {final} "
        f"({_human_size(final.stat().st_size)})."
    ]
    if q and pil_format in ("JPEG", "WEBP"):
        lines.append(f"Saved at quality {q}.")
    elif q:
        lines.append(f"quality doesn't apply to {pil_format} "
                     "(it's lossless) — the setting was ignored.")
    lines.extend(notes)
    note = _suffix_note(dest, final)
    if note:
        lines.append(note.strip())
    return " ".join(lines)


# ---------------------------------------------------------------------------
# SPLIT PDF
# ---------------------------------------------------------------------------


def _parse_page_spec(spec: str, total: int) -> list[tuple[list[int], str]] | str:
    """Parse a split spec into (page indexes, filename label) groups.

    Accepts 'each' (one group per page) or comma-separated 1-based
    page numbers and inclusive ranges, e.g. '1-3,5'. Every group
    becomes one output file. Returns an error string for a
    malformed or out-of-range spec — the caller writes nothing
    until the WHOLE spec has validated.
    """
    text = str(spec or "").strip()
    if not text:
        return ("No pages given — say which pages to split out, like "
                "'1-3,5', or 'each' for one file per page.")
    if text.lower() == "each":
        return [([i], f"p{i + 1}") for i in range(total)]
    groups: list[tuple[list[int], str]] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            return (f"That page spec has an empty entry: '{spec}'. "
                    "Use comma-separated pages or ranges, like '1-3,5'.")
        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2:
                return f"'{token}' isn't a page range I can read — use e.g. '2-4'."
            try:
                first, last = int(parts[0]), int(parts[1])
            except ValueError:
                return (f"'{token}' isn't a page range I can read — page "
                        "numbers must be whole numbers, like '2-4'.")
            if first > last:
                return (f"The range '{token}' runs backwards — give the "
                        "lower page first.")
            label = f"p{first}-{last}"
            pages = list(range(first, last + 1))
        else:
            try:
                only = int(token)
            except ValueError:
                return (f"'{token}' isn't a page number I can read — use "
                        "whole numbers, ranges like '2-4', or 'each'.")
            label = f"p{only}"
            pages = [only]
        for page_no in pages:
            if page_no < 1 or page_no > total:
                return (f"Page {page_no} doesn't exist — '{spec}' was "
                        f"checked against the real document, which has "
                        f"{total} page(s). Nothing was written.")
        groups.append(([p - 1 for p in pages], label))
    return groups


def split_pdf(path: str, pages: str, output_dir: str = "") -> str:
    """Split a PDF into separate files by page range.

    `pages` is '1-3,5' style (1-based, inclusive; each comma entry
    becomes one file) or 'each' (one file per page). The spec is
    validated against the document's real page count BEFORE
    anything is written — an out-of-range page refuses the whole
    split, naming the count. Outputs are '<stem>_p1-3.pdf' /
    '<stem>_p5.pdf' (or '<stem>_pN.pdf' per page for 'each') in
    output_dir (default: beside the input), never overwriting:
    taken names get a ' (2)'-style suffix.
    """
    target = _existing_file(path, "PDF file")
    if isinstance(target, str):
        return target
    pypdf, err = _load_pypdf()
    if err:
        return err
    try:
        reader = pypdf.PdfReader(str(target))
        total = len(reader.pages)
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}' couldn't be read as a PDF: {e}"
    if total == 0:
        return f"'{target.name}' has no pages to split."
    plan = _parse_page_spec(pages, total)
    if isinstance(plan, str):
        return plan
    if output_dir and str(output_dir).strip():
        out_dir = Path(str(output_dir)).expanduser()
    else:
        out_dir = target.parent
    if out_dir.exists() and not out_dir.is_dir():
        return f"The output location exists but is a file, not a folder: {out_dir}"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"Could not create the output folder {out_dir}: {e}"
    # Plan verification: the files about to be written must account
    # for exactly the pages the spec asked for — checked BEFORE the
    # first byte goes out, and re-checked against what was written.
    planned = sum(len(indexes) for indexes, _label in plan)
    written_files: list[tuple[Path, int]] = []
    written = 0
    try:
        for indexes, label in plan:
            writer = pypdf.PdfWriter()
            for index in indexes:
                writer.add_page(reader.pages[index])
            dest = out_dir / f"{target.stem}_{label}.pdf"
            final = _non_clobbering(dest)
            with open(final, "wb") as handle:
                writer.write(handle)
            writer.close()
            written += len(indexes)
            written_files.append((final, len(indexes)))
    except Exception as e:  # noqa: BLE001
        return (f"The split failed partway: {e}. "
                f"{len(written_files)} file(s) were already written to "
                f"{out_dir} — check there before retrying.")
    if written != planned:
        return (f"Something went wrong inside the split: the spec asked "
                f"for {planned} pages but {written} were written. Treat "
                "the output files as suspect.")
    # R1b: re-read every part file — writer.write returning does
    # not prove the parts landed on disk.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    for final, _count in written_files:
        ok, evidence = _outcome.file_written(final)
        if not ok:
            return (f"The split wrote {len(written_files)} part file(s) "
                    f"but one could not be confirmed: {evidence}. "
                    "Treat the output files as suspect.")
    lines = [f"Split '{target.name}' ({total} pages) into "
             f"{len(written_files)} file(s) in {out_dir}:"]
    for final, count in written_files:
        lines.append(f"  {final} — {count} page{'s' if count != 1 else ''}")
    return _outcome.verified(
        "\n".join(lines),
        "%d part file(s) re-read on disk" % len(written_files))


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Media II pack tool on a ToolRegistry-like object."""
    # Packaging batch: INTENTIONAL OVERRIDE — resize_image was
    # already registered by core/skills_media.py (mega pack,
    # registered earlier). Registry.register() is last-write-
    # wins, so THIS handler wins: it adds scale_percent, an
    # explicit output path, EXIF orientation, never-overwrite
    # suffixing, and the 20000-pixel cap the media version
    # lacks. Pinned by tests/test_packaging.py; do not reorder
    # the packs without updating that test.
    registry.register(
        name="resize_image",
        description="Resize ONE image (keeping its format), saving the result as a NEW file — the original is never touched. Give exactly ONE sizing mode: a width (height follows the aspect ratio), a height, both width and height (exact, and the result warns you if the picture's proportions were changed), or scale_percent (1–1000, e.g. 50 halves it). EXIF orientation is honoured, so sizes are the displayed sizes. Default output is '<name>_resized.<ext>' beside the input; an existing output name is never overwritten — a ' (2)'-style suffix is used and reported. No side may exceed 20000 pixels. Use when the user wants a picture made smaller or bigger.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to resize"},
                "width": {"type": "integer", "description": "Target width in pixels (0 = unused)"},
                "height": {"type": "integer", "description": "Target height in pixels (0 = unused)"},
                "scale_percent": {"type": "integer", "description": "Scale as a percentage, 1–1000 (0 = unused)"},
                "output": {"type": "string", "description": "Optional full path for the resized image (default: '<name>_resized.<ext>' beside the input)"},
            },
            "required": ["path"],
        },
        handler=resize_image,
        override=True,
    )
    # Packaging batch: INTENTIONAL OVERRIDE — convert_image was
    # already registered by core/skills_media.py (mega pack,
    # registered earlier). Registry.register() is last-write-
    # wins, so THIS handler wins: it adds an explicit output
    # path, a quality control, never-overwrite suffixing, EXIF
    # orientation, and the transparency-flattening warning the
    # media version lacks. Pinned by tests/test_packaging.py.
    registry.register(
        name="convert_image",
        description="Convert ONE image to a different format — png, jpg, webp or bmp — saving it as a NEW file with the new extension (the original is never overwritten; a taken name gets a ' (2)'-style suffix and is reported). An optional quality (1–95) controls jpg/webp output. Transparency is preserved where the format supports it; converting to jpg or bmp flattens transparency onto WHITE and the result says so. EXIF orientation is honoured, so the converted picture displays the same way. Use when the user wants a picture in another format (e.g. a PNG as a JPG).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the image file to convert"},
                "format": {
                    "type": "string",
                    "enum": ["png", "jpg", "webp", "bmp"],
                    "description": "The format to convert to",
                },
                "output": {"type": "string", "description": "Optional full path for the converted image (default: same name, new extension, beside the input)"},
                "quality": {"type": "integer", "description": "JPEG/WebP quality 1–95 (0 = encoder default); ignored for png/bmp"},
            },
            "required": ["path", "format"],
        },
        handler=convert_image,
        override=True,
    )
    registry.register(
        name="split_pdf",
        description="Split a PDF into separate PDF files by page. The pages spec is 1-based and inclusive: '1-3,5' makes one file with pages 1–3 and another with page 5; 'each' makes one file per page. The spec is checked against the document's real page count BEFORE anything is written — an out-of-range page refuses the split and names the count. Files are named '<stem>_p1-3.pdf', '<stem>_p5.pdf' (or '<stem>_pN.pdf'), written beside the input unless output_dir is given, and are never overwritten (a ' (2)'-style suffix is used and reported). The result lists every file written and its page count. Use when the user wants pages pulled out of a PDF into their own files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Full path of the PDF file to split"},
                "pages": {"type": "string", "description": "Which pages, 1-based: '1-3,5' style, or 'each' for one file per page"},
                "output_dir": {"type": "string", "description": "Optional folder for the split files (default: beside the input PDF)"},
            },
            "required": ["path", "pages"],
        },
        handler=split_pdf,
    )
