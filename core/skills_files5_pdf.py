import math
from datetime import datetime
from pathlib import Path
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_files5_helpers import (
    _SHEET_CAP,
    _SPLIT_PART_CAP,
    _existing_file,
    _existing_folder,
    _human_size,
    _list_images,
    _load_pil,
    _load_pypdf,
    _non_clobbering,
    _safe_stem,
    _suffix_note,
)


# ---------------------------------------------------------------------------
# PDF MERGE WITH BOOKMARKS — one outline entry per source file
# ---------------------------------------------------------------------------


def _count_outline_items(outline) -> int:
    """Count bookmark entries in a pypdf outline (nested lists)."""
    count = 0
    for item in outline or []:
        if isinstance(item, list):
            count += _count_outline_items(item)
        elif getattr(item, "title", None) is not None:
            count += 1
    return count


def pdf_merge_bookmarks(paths: str, output_path: str = "") -> str:
    """Merge PDFs (semicolon-separated paths), bookmarking each source."""
    raw = [part.strip() for part in str(paths or "").split(";")
           if part.strip()]
    if len(raw) < 2:
        return ("Merging with bookmarks needs at least two PDF "
                "files — give their paths as one list separated by "
                "semicolons, in the order they should appear.")
    targets: list[Path] = []
    for part in raw:
        found = _existing_file(part, "PDF file")
        if isinstance(found, str):
            return f"Merge stopped before anything was written — {found}"
        targets.append(found)
    pypdf, err = _load_pypdf()
    if err:
        return err
    readers = []
    page_counts = []
    for target in targets:
        try:
            reader = pypdf.PdfReader(str(target))
            if reader.is_encrypted:
                return (f"Merge stopped before anything was written — "
                        f"'{target.name}' is encrypted "
                        "(password-protected).")
            page_counts.append(len(reader.pages))
            readers.append(reader)
        except Exception as e:  # noqa: BLE001
            return (f"Merge stopped before anything was written — "
                    f"'{target.name}' couldn't be read as a PDF: {e}")
    if output_path and str(output_path).strip():
        dest = Path(str(output_path)).expanduser()
    else:
        dest = targets[0].parent / "merged_bookmarked.pdf"
    final = _non_clobbering(dest)
    try:
        writer = pypdf.PdfWriter()
        start = 0
        for target, reader, count in zip(targets, readers, page_counts):
            writer.append(reader)
            writer.add_outline_item(target.stem, start)
            start += count
        final.parent.mkdir(parents=True, exist_ok=True)
        with open(final, "wb") as handle:
            writer.write(handle)
        writer.close()
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()
        except OSError:
            pass
        return f"The merged PDF couldn't be written: {e}"
    # Verify after the act: re-open and count pages AND bookmarks.
    try:
        check = pypdf.PdfReader(str(final))
        written_pages = len(check.pages)
        written_marks = _count_outline_items(check.outline)
    except Exception as e:  # noqa: BLE001
        return (f"The merged PDF was written to {final} but couldn't "
                f"be re-opened to verify it: {e}")
    expected = sum(page_counts)
    if written_pages != expected:
        return (f"The merged PDF at {final} has {written_pages} "
                f"page(s) but the sources total {expected} — treating "
                "that as a failed merge.")
    names = ", ".join(f"'{t.name}' ({c}p)"
                      for t, c in zip(targets, page_counts))
    return (f"Merged {len(targets)} PDF(s) into {final} "
            f"({_human_size(final.stat().st_size)}): {written_pages} "
            f"page(s) total, verified by re-opening, with "
            f"{written_marks} bookmark(s) — one per source file. "
            f"Sources in order: {names}."
            f"{_suffix_note(dest, final)}")


# ---------------------------------------------------------------------------
# PDF SPLIT BY TEXT — a new part wherever the marker phrase appears
# ---------------------------------------------------------------------------


def _following_line(text: str, marker: str) -> str:
    """The first non-empty line AFTER the line holding the marker."""
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        if marker in line:
            for later in lines[index + 1:]:
                if later.strip():
                    return later.strip()
            return ""
    return ""


def pdf_split_by_text(path: str, marker: str, output_folder: str = "") -> str:
    """Split a PDF at every page whose text contains the marker."""
    target = _existing_file(path, "PDF file")
    if isinstance(target, str):
        return target
    marker = str(marker or "").strip()
    if not marker:
        return ("Splitting by text needs the marker phrase to look "
                "for (e.g. 'Invoice') — a new part starts at every "
                "page containing it.")
    pypdf, err = _load_pypdf()
    if err:
        return err
    try:
        reader = pypdf.PdfReader(str(target))
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}' couldn't be read as a PDF: {e}"
    if reader.is_encrypted:
        return (f"'{target.name}' is encrypted (password-protected) — "
                "it can't be split without the password.")
    total = len(reader.pages)
    if total == 0:
        return f"'{target.name}' has no pages — nothing to split."
    page_texts: list[str] = []
    for page in reader.pages:
        try:
            page_texts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — unreadable text = no marker
            page_texts.append("")
    starts = [i for i, text in enumerate(page_texts) if marker in text]
    if not starts:
        return (f"The marker '{marker}' doesn't appear in any of "
                f"'{target.name}'s {total} page(s) — nothing was "
                "split. (Matching is exact, including capitalisation, "
                "and scanned-image pages carry no text to search.)")
    bounds = list(starts)
    if bounds[0] > 0:
        bounds.insert(0, 0)   # the pages before the first marker
    if len(bounds) > _SPLIT_PART_CAP:
        return (f"'{marker}' starts {len(bounds)} parts in "
                f"'{target.name}' — the cap is {_SPLIT_PART_CAP} "
                "parts per split. Use a more specific marker phrase.")
    if output_folder and str(output_folder).strip():
        out_dir = Path(str(output_folder)).expanduser()
    else:
        out_dir = target.with_name(f"{target.stem}_split")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"Couldn't create the output folder '{out_dir}': {e}"
    written: list[tuple[str, int]] = []
    for part_no, start in enumerate(bounds, start=1):
        end = bounds[part_no] if part_no < len(bounds) else total
        if start in starts:
            label = _following_line(page_texts[start], marker)
            stem = _safe_stem(label, f"{target.stem}_part{part_no}")
        else:
            stem = f"{target.stem}_before_marker"
        final = _non_clobbering(out_dir / f"{stem}.pdf")
        try:
            writer = pypdf.PdfWriter()
            for page_index in range(start, end):
                writer.add_page(reader.pages[page_index])
            with open(final, "wb") as handle:
                writer.write(handle)
            writer.close()
        except Exception as e:  # noqa: BLE001
            return (f"Splitting '{target.name}' failed partway on "
                    f"part {part_no}: {e} ({len(written)} part(s) "
                    f"already written to {out_dir}).")
        # Verify each part as it lands: it must re-open with the
        # exact page count it was given.
        try:
            made = len(pypdf.PdfReader(str(final)).pages)
        except Exception as e:  # noqa: BLE001
            return (f"Part {part_no} ('{final.name}') was written "
                    f"but couldn't be re-opened to verify it: {e}")
        if made != end - start:
            return (f"Part {part_no} ('{final.name}') has {made} "
                    f"page(s) but should have {end - start} — "
                    "treating the split as failed.")
        written.append((final.name, made))
    lines = [f"Split '{target.name}' ({total} page(s)) into "
             f"{len(written)} part(s) in {out_dir}, split wherever "
             f"a page contains '{marker}' (verified: the parts' "
             f"pages add up to {sum(c for _n, c in written)}):"]
    for name, count in written:
        lines.append(f"• {name} — {count} page(s)")
    if bounds[0] == 0 and starts[0] != 0:
        lines.append("The first part holds the pages before the "
                     "first marker.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# IMAGE CONTACT SHEET — a folder's photos as one labelled grid
# ---------------------------------------------------------------------------


def image_contact_sheet(folder: str, columns: int = 0) -> str:
    """Grid a folder's images into one labelled contact_sheet.jpg."""
    root = _existing_folder(folder, "photo folder")
    if isinstance(root, str):
        return root
    Image, err = _load_pil()
    if err:
        return err
    try:
        from PIL import ImageDraw, ImageFont
    except ImportError:
        return "Contact sheets need Pillow's drawing pieces, which aren't available."
    try:
        columns = int(columns)
    except (TypeError, ValueError):
        return f"columns must be a whole number (got {columns!r})."
    if columns < 0:
        return "columns can't be negative — use 0 for an automatic near-square grid."
    candidates = _list_images(root)
    if not candidates:
        return (f"No images found in '{root}' — a contact sheet needs "
                "at least one PNG, JPEG, GIF, BMP, WEBP or TIFF file "
                "directly in that folder.")
    over_cap = len(candidates) > _SHEET_CAP
    candidates = candidates[:_SHEET_CAP]
    loaded: list[tuple[Path, object]] = []
    skipped: list[str] = []
    for candidate in candidates:
        try:
            image = Image.open(candidate)
            image.load()
            loaded.append((candidate, image.convert("RGB")))
        except Exception:  # noqa: BLE001 — unreadable files are skipped, named
            skipped.append(candidate.name)
    if not loaded:
        return (f"None of the image file(s) in '{root}' could be "
                "read — the contact sheet wasn't made.")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 13)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 11)
    except Exception:  # noqa: BLE001 — fall back to Pillow's built-in
        font = ImageFont.load_default()
        font_small = font
    cell_w, thumb_h, label_h = 264, 200, 38
    cols = columns if columns > 0 else math.ceil(math.sqrt(len(loaded)))
    cols = min(cols, len(loaded))
    rows = math.ceil(len(loaded) / cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * (thumb_h + label_h)),
                      (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for index, (candidate, image) in enumerate(loaded):
        cell_x = (index % cols) * cell_w
        cell_y = (index // cols) * (thumb_h + label_h)
        thumb = image.copy()
        thumb.thumbnail((cell_w - 8, thumb_h - 8),
                        Image.Resampling.LANCZOS)
        sheet.paste(thumb,
                    (cell_x + (cell_w - thumb.size[0]) // 2,
                     cell_y + (thumb_h - thumb.size[1]) // 2))
        name = candidate.name
        if len(name) > 34:
            name = name[:33] + "…"
        try:
            stamp = datetime.fromtimestamp(
                candidate.stat().st_mtime).strftime("%d %b %Y")
        except OSError:
            stamp = "date unknown"
        draw.text((cell_x + 6, cell_y + thumb_h + 3), name,
                  fill=(235, 235, 235), font=font)
        draw.text((cell_x + 6, cell_y + thumb_h + 20), stamp,
                  fill=(160, 160, 170), font=font_small)
    dest = root / "contact_sheet.jpg"
    final = _non_clobbering(dest)
    try:
        sheet.save(final, format="JPEG", quality=88)
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()
        except OSError:
            pass
        return f"The contact sheet couldn't be written: {e}"
    if not final.exists():
        return "The contact sheet was written but can't be found on disk — treating that as a failure."
    line = (f"Contact sheet saved to {final} "
            f"({_human_size(final.stat().st_size)}): {len(loaded)} "
            f"photo(s) in a {cols}×{rows} grid, each labelled with "
            f"its filename and date.{_suffix_note(dest, final)}")
    if over_cap:
        line += (f" Note: the folder holds more images — the sheet "
                 f"shows the first {_SHEET_CAP} in name order.")
    if skipped:
        line += (f" Skipped {len(skipped)} file(s) that couldn't be "
                 f"read as images: {', '.join(skipped[:5])}"
                 f"{' (+more)' if len(skipped) > 5 else ''}.")
    return line
