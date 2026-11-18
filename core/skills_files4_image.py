"""Image group of the skills_files_media_ops pack (moved verbatim)."""

import math
from pathlib import Path

# INTENTIONAL: cross-part names — the image group resolves these bare
# names in the helpers module's globals (bare-name rule, plan section 2 P3),
# not in the facade's.
from .skills_files4_helpers import (
    _IMAGE_EXTS,
    _PDF_IMAGE_LIST_CAP,
    _existing_file,
    _existing_folder,
    _fmt_rgb,
    _human_size,
    _load_numpy,
    _load_pil,
    _load_pypdf,
    _non_clobbering,
    _sha256_file,
    _suffix_note,
)

# ---------------------------------------------------------------------------
# IMAGE COMPARE — two images, judged by their pixels (read-only)
# ---------------------------------------------------------------------------


def image_compare(path_a: str, path_b: str) -> str:
    """Compare two images: facts, mean colour distance, similarity verdict."""
    target_a = _existing_file(path_a, "first image file")
    if isinstance(target_a, str):
        return target_a
    target_b = _existing_file(path_b, "second image file")
    if isinstance(target_b, str):
        return target_b
    Image, err = _load_pil()
    if err:
        return err
    np, err = _load_numpy()
    if err:
        return err
    try:
        img_a = Image.open(target_a)
        img_a.load()
        img_b = Image.open(target_b)
        img_b.load()
    except Exception as e:  # noqa: BLE001
        return (f"One of those files couldn't be read as an image: {e}. "
                "It may be corrupt or in a format Pillow doesn't support.")
    size_a_bytes = target_a.stat().st_size
    size_b_bytes = target_b.stat().st_size
    lines = [
        f"A '{target_a.name}': {img_a.size[0]}×{img_a.size[1]}, "
        f"format {img_a.format or 'unknown'}, {_human_size(size_a_bytes)}.",
        f"B '{target_b.name}': {img_b.size[0]}×{img_b.size[1]}, "
        f"format {img_b.format or 'unknown'}, {_human_size(size_b_bytes)}.",
    ]
    try:
        if _sha256_file(target_a) == _sha256_file(target_b):
            lines.append("Verdict: these are the SAME file contents — "
                         "identical, byte for byte (SHA-256 match), "
                         "whatever the names say.")
            return "\n".join(lines)
    except OSError as e:
        return f"Couldn't hash the files for comparison: {e}"
    # Per-channel means on the real pixels (thumbnails are only for
    # the difference pass — the means use a 1x1 reduction of the
    # full image, which is the true average colour).
    rgb_a = img_a.convert("RGB")
    rgb_b = img_b.convert("RGB")
    mean_a = rgb_a.resize((1, 1)).getpixel((0, 0))
    mean_b = rgb_b.resize((1, 1)).getpixel((0, 0))
    lines.append(f"Average colour: A is RGB {_fmt_rgb(mean_a)}, "
                 f"B is RGB {_fmt_rgb(mean_b)}.")
    thumb_a = np.asarray(rgb_a.resize((64, 64)), dtype=np.float64)
    thumb_b = np.asarray(rgb_b.resize((64, 64)), dtype=np.float64)
    distance = float(np.mean(np.abs(thumb_a - thumb_b)))  # 0..255 per channel
    lines.append(f"Mean pixel distance: {distance:.1f} out of 255 "
                 f"({distance / 255 * 100:.1f}% of full scale), measured "
                 "on 64×64 reductions of both images.")
    noticeably = float(np.mean(
        np.max(np.abs(thumb_a - thumb_b), axis=2) > 10)) * 100
    lines.append(f"Pixels noticeably different (some channel off by "
                 f"more than 10 of 255): {noticeably:.1f}% of the "
                 "64×64 comparison grid.")
    same_dims = img_a.size == img_b.size
    if not same_dims:
        lines.append(f"The dimensions differ (A is {img_a.size[0]}×"
                     f"{img_a.size[1]}, B is {img_b.size[0]}×"
                     f"{img_b.size[1]}), so this is a look-alike "
                     "comparison, not a pixel-perfect one.")
    if same_dims and distance < 0.5:
        verdict = ("nearly identical — same dimensions and the pixels "
                   "differ by less than half a step on average (the "
                   "files differ, e.g. in metadata or encoding, but "
                   "the pictures are effectively the same).")
    elif distance < 2.5:
        verdict = ("nearly identical — only the faintest differences "
                   "(a re-save or a tiny edit).")
    elif distance < 10:
        verdict = "very similar — clearly the same picture with edits or a quality change."
    elif distance < 30:
        verdict = "somewhat similar — related pictures, but substantially different."
    else:
        verdict = "quite different pictures."
    lines.append(f"Verdict: {verdict}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SPRITE SHEET — a folder of images tiled into one grid PNG
# ---------------------------------------------------------------------------


def _list_images(folder: Path) -> list[Path]:
    """Image files directly in a folder, name-sorted; nothing recursed."""
    return sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in _IMAGE_EXTS),
        key=lambda p: p.name.lower())


def make_sprite_sheet(folder: str, columns: int = 0, output_path: str = "") -> str:
    """Tile every image in a folder into one grid PNG, cells padded, never cropped."""
    root = _existing_folder(folder, "image folder")
    if isinstance(root, str):
        return root
    Image, err = _load_pil()
    if err:
        return err
    try:
        columns = int(columns)
    except (TypeError, ValueError):
        return f"columns must be a whole number (got {columns!r})."
    if columns < 0:
        return "columns can't be negative — use 0 for an automatic near-square grid."
    candidates = _list_images(root)
    if not candidates:
        return (f"No images found in '{root}' — a sprite sheet needs at "
                "least one PNG, JPEG, GIF, BMP, WEBP or TIFF file "
                "directly in that folder.")
    loaded: list = []
    skipped: list[str] = []
    for candidate in candidates:
        try:
            image = Image.open(candidate)
            image.load()
            loaded.append((candidate.name, image.convert("RGBA")))
        except Exception:  # noqa: BLE001 — unreadable files are skipped, named
            skipped.append(candidate.name)
    if not loaded:
        return (f"None of the {len(candidates)} image file(s) in '{root}' "
                "could be read — the sheet wasn't made.")
    cell_w = max(img.size[0] for _n, img in loaded)
    cell_h = max(img.size[1] for _n, img in loaded)
    cols = columns if columns > 0 else math.ceil(math.sqrt(len(loaded)))
    cols = min(cols, len(loaded))
    rows = math.ceil(len(loaded) / cols)
    sheet = Image.new("RGBA", (cols * cell_w, rows * cell_h), (0, 0, 0, 0))
    for index, (_name, image) in enumerate(loaded):
        cell_x = (index % cols) * cell_w
        cell_y = (index // cols) * cell_h
        # Centred in its cell with transparent padding — sprites keep
        # their native size; nothing is scaled or cropped (that is
        # what separates a sprite sheet from make_collage).
        sheet.paste(image,
                    (cell_x + (cell_w - image.size[0]) // 2,
                     cell_y + (cell_h - image.size[1]) // 2),
                    image)
    if output_path and str(output_path).strip():
        dest = Path(str(output_path)).expanduser()
    else:
        dest = root / "sprite_sheet.png"
    final = _non_clobbering(dest)
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(final, format="PNG")
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()
        except OSError:
            pass
        return f"The sprite sheet couldn't be written: {e}"
    line = (f"Sprite sheet saved to {final} "
            f"({_human_size(final.stat().st_size)}): {len(loaded)} image(s) "
            f"in a {cols}×{rows} grid of {cell_w}×{cell_h} cells "
            f"(sheet is {sheet.size[0]}×{sheet.size[1]}). Each image "
            "keeps its own size, centred in its cell with transparent "
            "padding — nothing was scaled or cropped."
            f"{_suffix_note(dest, final)}")
    if skipped:
        line += (f" Skipped {len(skipped)} file(s) that couldn't be "
                 f"read as images: {', '.join(skipped[:5])}"
                 f"{' (+more)' if len(skipped) > 5 else ''}.")
    return line


# ---------------------------------------------------------------------------
# BATCH WATERMARK — the single-image stamp, applied to a folder
# ---------------------------------------------------------------------------


_BATCH_POSITIONS = ("bottom_right", "bottom_left", "top_right",
                    "top_left", "center")  # mirrors skills_photo's set


def batch_watermark(folder: str, text: str, output_folder: str = "",
                    position: str = "bottom_right",
                    opacity: float = 0.5, cancel_token=None) -> str:
    """Watermark every image in a folder into a separate output folder.

    B20: ``cancel_token`` is checked per image — a Stop pressed
    mid-batch ends it promptly and returns the cancelled DICT
    (a string return would wrap as success). Stamped copies are
    complete files (the single-image stamp writes them whole);
    the originals are never touched.
    """
    text = str(text or "")
    if not text.strip():
        return "The watermark needs some text — an empty watermark stamps nothing."
    position = str(position or "").strip().lower()
    if position not in _BATCH_POSITIONS:
        return ("position must be one of: "
                + ", ".join(_BATCH_POSITIONS)
                + f" (got '{position}').")
    try:
        opacity = float(opacity)
    except (TypeError, ValueError):
        return "opacity must be a number between 0.1 and 1.0."
    if not 0.1 <= opacity <= 1.0:
        return (f"opacity must be between 0.1 and 1.0 (got "
                f"{opacity:g}) — 0.5 is a typical watermark.")
    root = _existing_folder(folder, "image folder")
    if isinstance(root, str):
        return root
    if output_folder and str(output_folder).strip():
        out_dir = Path(str(output_folder)).expanduser()
    else:
        out_dir = root / "watermarked"
    if out_dir.resolve() == root.resolve():
        return ("The watermarked copies need their own folder — writing "
                "them beside the originals under the same names would "
                "clobber the inputs. Leave output_folder empty to use a "
                "'watermarked' subfolder.")
    candidates = _list_images(root)
    if not candidates:
        return (f"No images found in '{root}' — nothing to watermark. "
                "I look for PNG, JPEG, GIF, BMP, WEBP and TIFF files "
                "directly in that folder.")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"Couldn't create the output folder '{out_dir}': {e}"
    # The stamp itself is the single-image tool's, imported lazily
    # (the desktop-pack pattern) — the same draw, the same
    # never-overwrite save, at the batch's position/opacity (the
    # single tool's own defaults when the caller doesn't set them).
    from .skills_photo import watermark_image

    processed = 0
    skipped: list[str] = []
    for candidate in candidates:
        # B20: the per-image yield point — a Stop pressed
        # mid-batch ends the loop here. Stamped copies are
        # complete files; the rest never ran.
        if cancel_token is not None and cancel_token.cancelled:
            from .funnel.tool_result import cancelled_result
            return cancelled_result(
                f"Watermarking in '{root}' was cancelled at the user's "
                f"request after {processed} of {len(candidates)} "
                "image(s) — the stamped copies are complete; the "
                "rest were not stamped. The originals are untouched.")
        result = watermark_image(str(candidate), text,
                                 position=position, opacity=opacity,
                                 output=str(out_dir / candidate.name))
        if isinstance(result, str) and result.startswith("Watermarked"):
            processed += 1
        else:
            skipped.append(candidate.name)
    settings = ""
    if position != "bottom_right" or opacity != 0.5:
        settings = f" (stamped at {position}, opacity {opacity:g})"
    line = (f"Watermarked {processed} of {len(candidates)} image(s) "
            f"with '{text}'{settings} into {out_dir} — the copies went there "
            "under the originals' names (suffixed where a name was "
            "already taken). The originals in '{root}' are untouched.")
    if skipped:
        line += (f" Skipped {len(skipped)} file(s) that couldn't be "
                 f"stamped: {', '.join(skipped[:5])}"
                 f"{' (+more)' if len(skipped) > 5 else ''}.")
    if processed == 0:
        line = (f"Nothing could be watermarked in '{root}' — all "
                f"{len(candidates)} candidate file(s) failed to stamp "
                f"(first: {skipped[0] if skipped else 'unknown'}). "
                "The output folder was created but holds no new images.")
    return line


# ---------------------------------------------------------------------------
# PDF EXTRACT IMAGES — pull a PDF's embedded pictures out to files
# ---------------------------------------------------------------------------


def pdf_extract_images(path: str, output_folder: str = "") -> str:
    """Extract every embedded image from a PDF into a folder."""
    target = _existing_file(path, "PDF file")
    if isinstance(target, str):
        return target
    pypdf, err = _load_pypdf()
    if err:
        return err
    try:
        reader = pypdf.PdfReader(str(target))
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}' couldn't be read as a PDF: {e}"
    if reader.is_encrypted:
        return (f"'{target.name}' is encrypted (password-protected) — "
                "its embedded images can't be extracted without the "
                "password.")
    if output_folder and str(output_folder).strip():
        out_dir = Path(str(output_folder)).expanduser()
    else:
        out_dir = target.with_name(f"{target.stem}_images")
    try:
        total_pages = len(reader.pages)
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}'s pages couldn't be read: {e}"
    written: list[str] = []
    total_bytes = 0
    failures = 0
    try:
        for page in reader.pages:
            try:
                images = list(page.images)
            except Exception:  # noqa: BLE001 — one bad page shouldn't sink the rest
                failures += 1
                continue
            for image_file in images:
                try:
                    data = image_file.data
                except Exception:  # noqa: BLE001
                    failures += 1
                    continue
                if not data:
                    failures += 1
                    continue
                name = Path(str(image_file.name or "image")).name or "image"
                if not Path(name).suffix:
                    name += ".bin"
                out_dir.mkdir(parents=True, exist_ok=True)
                final = _non_clobbering(out_dir / name)
                try:
                    final.write_bytes(data)
                    written.append(final.name)
                    total_bytes += len(data)
                except OSError:
                    failures += 1
    except Exception as e:  # noqa: BLE001
        return (f"Extracting from '{target.name}' failed partway: {e} "
                f"({len(written)} image(s) had been written to {out_dir}).")
    if not written:
        if failures:
            return (f"'{target.name}' has embedded images but none of "
                    f"them could be extracted ({failures} failure(s)) — "
                    "they may use an encoding pypdf can't decode.")
        return (f"No embedded images found in '{target.name}' — it has "
                f"{total_pages} page(s), but none carry image data to "
                "extract. (A scanned page IS an image; a page with no "
                "image objects may just be drawn shapes and text.)")
    shown = written[:_PDF_IMAGE_LIST_CAP]
    extra = (f" …and {len(written) - len(shown)} more"
             if len(written) > len(shown) else "")
    line = (f"Extracted {len(written)} image(s) from '{target.name}' "
            f"into {out_dir}: {', '.join(shown)}{extra}. "
            f"Total extracted: {_human_size(total_bytes)}.")
    if failures:
        line += f" {failures} embedded image(s) couldn't be decoded and were skipped."
    return line
