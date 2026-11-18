# Skills split Phase A batch A3 INTENTIONAL: inspect group moved verbatim
# from core/skills_files_inspect.py. House helpers are imported from the
# helpers module (bare-name rule: a moved function resolves its bare
# names in its defining module's globals, not the facade's).

import os
import re
from pathlib import Path
from .skills_files3_helpers import (
    _HASH_CHUNK,
    _human_size,
    _existing_file,
    _existing_folder,
    _non_clobbering,
    _suffix_note,
    _load_pil,
    _load_pypdf,
    _sha256_file,
)


# ---------------------------------------------------------------------------
# FOLDER DIFF
# ---------------------------------------------------------------------------

_DIFF_MAX_FILES_PER_SIDE = 2000  # files walked per folder before we stop
_DIFF_MAX_HASH_PAIRS = 500       # same-size pairs hash-checked per compare
_DIFF_LIST_CAP = 25              # names shown per category in the result


def _scan_tree(root: Path):
    """Walk one folder into {relative path: (path, size)}.

    Returns (inventory, empty_dirs, capped, symlinks_skipped, errors).
    Symlinks are skipped, never followed (the sync_folder posture).
    Empty directories — folders holding nothing at all — are counted
    only; there is nothing inside them to compare. The walk stops
    at _DIFF_MAX_FILES_PER_SIDE files and says so via `capped`, so
    a partial compare is always disclosed, never silent.
    """
    inventory: dict[str, tuple[Path, int]] = {}
    empty_dirs = 0
    capped = False
    symlinks_skipped = 0
    errors = 0
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        kept_dirs = []
        for dirname in dirnames:
            if (current / dirname).is_symlink():
                symlinks_skipped += 1
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        if current != root:
            try:
                if not os.listdir(current):
                    empty_dirs += 1
            except OSError:
                pass
        for filename in filenames:
            if len(inventory) >= _DIFF_MAX_FILES_PER_SIDE:
                capped = True
                break
            item = current / filename
            if item.is_symlink():
                symlinks_skipped += 1
                continue
            try:
                size = item.stat().st_size
            except OSError:
                errors += 1
                continue
            inventory[item.relative_to(root).as_posix()] = (item, size)
        if capped:
            dirnames[:] = []
    return inventory, empty_dirs, capped, symlinks_skipped, errors


def _diff_inventories(inv_a, inv_b, hash_fn, hash_cap: int) -> dict:
    """Compare two inventories (pure apart from hash_fn's reads).

    A file present in both is DIFFERENT when its sizes differ, or
    when the sizes match and the SHA-256 hashes don't. Same-size
    pairs are hash-checked only up to `hash_cap`; pairs past the
    cap are counted `unverified` — reported as neither different
    nor proven identical.
    """
    only_a = sorted(set(inv_a) - set(inv_b))
    only_b = sorted(set(inv_b) - set(inv_a))
    different: list[str] = []
    identical = 0
    unverified = 0
    hash_errors = 0
    hashed = 0
    for rel in sorted(set(inv_a) & set(inv_b)):
        path_a, size_a = inv_a[rel]
        path_b, size_b = inv_b[rel]
        if size_a != size_b:
            different.append(rel)
            continue
        if hashed >= hash_cap:
            unverified += 1
            continue
        hashed += 1
        try:
            if hash_fn(path_a) != hash_fn(path_b):
                different.append(rel)
            else:
                identical += 1
        except OSError:
            hash_errors += 1
    return {
        "only_a": only_a,
        "only_b": only_b,
        "different": different,
        "identical": identical,
        "unverified": unverified,
        "hash_errors": hash_errors,
        "hashed": hashed,
    }


def _format_name_list(names, cap: int = _DIFF_LIST_CAP) -> str:
    """Up to `cap` names, with any remainder disclosed, never dumped."""
    if len(names) <= cap:
        return ", ".join(names)
    return ", ".join(names[:cap]) + f" (+{len(names) - cap} more)"


def folder_diff(path_a: str, path_b: str) -> str:
    """Recursively compare two folders: only-in-A, only-in-B, changed."""
    root_a = _existing_folder(path_a, "first folder")
    if isinstance(root_a, str):
        return root_a
    root_b = _existing_folder(path_b, "second folder")
    if isinstance(root_b, str):
        return root_b
    if root_a.resolve() == root_b.resolve():
        return ("Those two paths are the same folder — comparing a "
                "folder with itself can't show any differences. "
                "Give two different folders.")
    inv_a, empty_a, capped_a, sym_a, err_a = _scan_tree(root_a)
    inv_b, empty_b, capped_b, sym_b, err_b = _scan_tree(root_b)
    outcome = _diff_inventories(inv_a, inv_b, _sha256_file, _DIFF_MAX_HASH_PAIRS)
    common = len(set(inv_a) & set(inv_b))
    lines = [
        f"Compared A '{root_a}' ({len(inv_a)} file(s)) with B '{root_b}' "
        f"({len(inv_b)} file(s)): {common} file(s) appear in both."
    ]
    if capped_a or capped_b:
        sides = ("both folders" if capped_a and capped_b
                 else "folder A" if capped_a else "folder B")
        lines.append(
            f"Note: the scan of {sides} stopped at the "
            f"{_DIFF_MAX_FILES_PER_SIDE}-file cap, so files past the "
            "cap weren't compared — treat this as a partial result.")
    if outcome["only_a"]:
        lines.append(
            f"Only in A ({len(outcome['only_a'])}): "
            f"{_format_name_list(outcome['only_a'])}")
    if outcome["only_b"]:
        lines.append(
            f"Only in B ({len(outcome['only_b'])}): "
            f"{_format_name_list(outcome['only_b'])}")
    if outcome["different"]:
        lines.append(
            f"In both but DIFFERENT ({len(outcome['different'])}): "
            f"{_format_name_list(outcome['different'])}")
    if not (outcome["only_a"] or outcome["only_b"] or outcome["different"]):
        if common == 0:
            lines.append("Neither folder contains any files — nothing differs.")
        elif outcome["unverified"]:
            lines.append(
                f"No differences found among the files checked — but "
                f"{outcome['unverified']} same-size pair(s) weren't "
                f"hash-checked (the {_DIFF_MAX_HASH_PAIRS}-pair cap), "
                "so this isn't a proof the folders are identical.")
        elif outcome["hash_errors"]:
            lines.append(
                f"No differences found among the files checked — but "
                f"{outcome['hash_errors']} file(s) couldn't be read "
                "for hashing, so this isn't a proof the folders "
                "are identical.")
        else:
            lines.append(
                f"The two folders are IDENTICAL: all {common} shared "
                "file(s) match by SHA-256 (a size match alone never "
                "counts as a match here).")
    else:
        lines.append(
            f"{outcome['identical']} shared file(s) verified identical "
            "by SHA-256.")
        if outcome["unverified"]:
            lines.append(
                f"{outcome['unverified']} same-size pair(s) weren't "
                f"hash-checked (the {_DIFF_MAX_HASH_PAIRS}-pair cap) — "
                "they aren't counted as different, or as proven "
                "identical.")
    if empty_a or empty_b:
        lines.append(
            f"Empty folders (nothing inside to compare): {empty_a} "
            f"in A, {empty_b} in B.")
    skipped = sym_a + sym_b
    if skipped:
        lines.append(f"{skipped} symlink(s) were skipped, not followed.")
    read_errors = err_a + err_b + outcome["hash_errors"]
    if read_errors and (outcome["only_a"] or outcome["only_b"]
                         or outcome["different"] or outcome["unverified"]):
        lines.append(f"{read_errors} file(s) couldn't be read while comparing.")
    if inv_a or inv_b:
        total_a = sum(size for _p, size in inv_a.values())
        total_b = sum(size for _p, size in inv_b.values())
        lines.append(
            f"Total size: A {_human_size(total_a)}, "
            f"B {_human_size(total_b)}.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# STRIP IMAGE METADATA
# ---------------------------------------------------------------------------


def _exif_summary(image) -> tuple[int, bool, object]:
    """(EXIF tag count, GPS present, orientation value).

    Counts and presence ONLY — no tag value ever leaves this
    helper in a result (image_info's privacy precedent). The
    count covers the top-level EXIF block plus the EXIF and GPS
    sub-blocks, so '14 tags incl. GPS' is an honest total.
    """
    try:
        exif = image.getexif()
    except Exception:  # noqa: BLE001
        return 0, False, None
    if not exif:
        return 0, False, None
    count = len(exif)
    gps_present = False
    for ifd_id in (0x8769, 0x8825):  # EXIF sub-IFD, GPS sub-IFD
        try:
            sub = exif.get_ifd(ifd_id)
        except Exception:  # noqa: BLE001
            sub = None
        if sub:
            count += len(sub)
            if ifd_id == 0x8825:
                gps_present = True
    return count, gps_present, exif.get(274)


def strip_image_metadata(path: str, output: str = "") -> str:
    """Save a copy of an image with EXIF / GPS / text metadata removed."""
    target = _existing_file(path, "image file")
    if isinstance(target, str):
        return target
    Image, err = _load_pil()
    if err:
        return err
    try:
        image = Image.open(target)
        image.load()
    except Exception as e:  # noqa: BLE001
        return (f"Could not read '{target.name}' as an image: {e}. "
                "The file may be corrupt or in a format Pillow "
                "doesn't support.")
    # Measure the metadata BEFORE orienting (orienting itself
    # rewrites the orientation tag).
    tag_count, gps_present, orientation = _exif_summary(image)
    icc_present = bool(image.info.get("icc_profile"))
    try:
        text_chunks = len(getattr(image, "text", None) or {})
    except Exception:  # noqa: BLE001
        text_chunks = 0
    # Bake the EXIF orientation into the pixels FIRST (the v4.7
    # rule) — strip the tag without baking it and portrait photos
    # come out lying on their side.
    try:
        from PIL import ImageOps

        oriented = ImageOps.exif_transpose(image)
    except Exception:  # noqa: BLE001 — no usable EXIF: stored pixels rule
        oriented = image
    # Rebuild from raw pixels: the new image is born with an empty
    # info dict, so no EXIF, ICC profile or text chunk can ride
    # along. A palette is image data, not metadata — keep it, or
    # palette images would lose their colours.
    clean = Image.frombytes(oriented.mode, oriented.size, oriented.tobytes())
    if oriented.mode == "P":
        try:
            palette = oriented.getpalette()
            if palette:
                clean.putpalette(palette)
        except Exception:  # noqa: BLE001
            pass
    if output and str(output).strip():
        dest = Path(str(output)).expanduser()
    else:
        dest = target.with_name(f"{target.stem}_clean{target.suffix}")
    final = _non_clobbering(dest)
    try:
        if final.suffix:
            clean.save(final)
        else:  # no extension to infer from — keep the source format
            clean.save(final, format=oriented.format or "PNG")
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()  # never leave a half-written image behind
        except OSError:
            pass
        return f"The cleaned copy couldn't be written: {e}"
    removed: list[str] = []
    if tag_count:
        piece = f"EXIF block ({tag_count} tag{'s' if tag_count != 1 else ''}"
        piece += ", incl. GPS data" if gps_present else ""
        removed.append(piece + ")")
    if icc_present:
        removed.append("ICC colour profile")
    if text_chunks:
        removed.append(f"{text_chunks} embedded text chunk"
                       f"{'s' if text_chunks != 1 else ''}")
    width, height = clean.size
    if removed:
        summary = (f"Removed: {'; '.join(removed)}. ")
    else:
        summary = ("The file carried no EXIF, GPS, ICC or text metadata, "
                   "so nothing needed removing — the copy is a plain "
                   "pixel-for-pixel rebuild. ")
    baked = ""
    if orientation not in (None, 1, "1"):
        baked = ("The EXIF orientation was baked into the pixels before "
                 "stripping, so the copy displays the same way up. ")
    # Read-back verification: re-open the saved copy and recount
    # its metadata — the tool's whole promise is that none remains.
    verify = ""
    try:
        saved = Image.open(final)
        saved.load()
        left_count, _left_gps, _left_orient = _exif_summary(saved)
        left_icc = bool(saved.info.get("icc_profile"))
        try:
            left_text = len(getattr(saved, "text", None) or {})
        except Exception:  # noqa: BLE001
            left_text = 0
        leftovers: list[str] = []
        if left_count:
            leftovers.append(
                f"{left_count} EXIF tag{'s' if left_count != 1 else ''}")
        if left_icc:
            leftovers.append("an ICC profile")
        if left_text:
            leftovers.append(
                f"{left_text} text chunk{'s' if left_text != 1 else ''}")
        if leftovers:
            verify = (f" Verification by re-reading the copy found "
                      f"{' and '.join(leftovers)} still present — "
                      "don't treat it as fully clean.")
        else:
            verify = (" Verified by re-reading the copy: no EXIF, "
                      "GPS, ICC or text metadata remains.")
    except Exception:  # noqa: BLE001
        verify = (" (The copy couldn't be re-read to verify the "
                  "metadata is gone.)")
    return (f"Saved a metadata-free copy of '{target.name}' "
            f"({width}×{height}) to {final} "
            f"({_human_size(final.stat().st_size)}). {summary}{baked}"
            f"The original is untouched.{_suffix_note(dest, final)}"
            f"{verify}")


# ---------------------------------------------------------------------------
# PDF INFO
# ---------------------------------------------------------------------------


def _points_to_mm(points: float) -> float:
    return points * 25.4 / 72.0


def pdf_info(path: str) -> str:
    """Report a PDF's facts: version, pages, page size, metadata, encryption."""
    target = _existing_file(path, "PDF file")
    if isinstance(target, str):
        return target
    file_size = target.stat().st_size
    version = None
    try:
        with open(target, "rb") as handle:
            head = handle.read(1024)
        match = re.search(rb"%PDF-(\d+)\.(\d+)", head)
        if match:
            version = f"{match.group(1).decode()}.{match.group(2).decode()}"
    except OSError:
        pass
    pypdf, err = _load_pypdf()
    if err:
        return err
    try:
        reader = pypdf.PdfReader(str(target))
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}' couldn't be read as a PDF: {e}"
    header = (f"'{target.name}': PDF {version or 'version unknown'}, "
              f"{_human_size(file_size)} on disk.")
    if reader.is_encrypted:
        # The header is all an encrypted document will tell us
        # without its password — page count and metadata stay shut.
        return (f"{header} Encrypted: yes — a password is needed to "
                "open it, so the page count and document details "
                "can't be read.")
    try:
        total = len(reader.pages)
    except Exception as e:  # noqa: BLE001
        return f"{header} Its pages couldn't be read: {e}"
    lines = [header]
    facts = f"{total} page{'s' if total != 1 else ''}."
    if total:
        try:
            box = reader.pages[0].mediabox
            width_pt, height_pt = float(box.width), float(box.height)
            facts += (f" First page: {_points_to_mm(width_pt):.1f}×"
                      f"{_points_to_mm(height_pt):.1f} mm "
                      f"({width_pt:.0f}×{height_pt:.0f} pt).")
        except Exception:  # noqa: BLE001
            pass
    lines.append(facts)
    try:
        metadata = reader.metadata
    except Exception:  # noqa: BLE001
        metadata = None
    if metadata:
        def _meta_text(value) -> str:
            if value is None:
                return ""
            if isinstance(value, (list, tuple)):
                return ", ".join(
                    str(v).strip() for v in value if str(v).strip())
            if hasattr(value, "strftime"):
                try:
                    return value.strftime("%Y-%m-%d %H:%M")
                except Exception:  # noqa: BLE001
                    return str(value).strip()
            return str(value).strip()

        shown = []
        for label, attr in (("Title", "title"),
                            ("Author", "author"),
                            ("Subject", "subject"),
                            ("Keywords", "keywords"),
                            ("Creator", "creator"),
                            ("Producer", "producer"),
                            ("Created", "creation_date"),
                            ("Modified", "modification_date")):
            try:
                value = _meta_text(getattr(metadata, attr, None))
            except Exception:  # noqa: BLE001
                value = ""
            if value:
                shown.append(f"{label}: '{value}'")
        if shown:
            lines.append(". ".join(shown) + ".")
    lines.append("Not encrypted.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ROTATE PDF
# ---------------------------------------------------------------------------


def _parse_page_selection(spec: str, total: int) -> set[int] | str:
    """Parse a rotate page selection into 0-based page indexes.

    'all', or comma-separated 1-based page numbers and inclusive
    ranges like '1-3,5' — the same posture as split_pdf's spec
    (factored here, not imported: packs don't import each other).
    Every page is checked against the document's REAL page count,
    and any problem returns an error string — the caller writes
    nothing until the whole selection has validated.
    """
    text = str(spec or "").strip()
    if text.lower() == "all":
        return set(range(total))
    if not text:
        return ("No pages given — say 'all' or which pages to rotate, "
                "like '1-3,5'. Nothing was written.")
    selected: set[int] = set()
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
            pages = list(range(first, last + 1))
        else:
            try:
                only = int(token)
            except ValueError:
                return (f"'{token}' isn't a page number I can read — use "
                        "whole numbers, ranges like '2-4', or 'all'.")
            pages = [only]
        for page_no in pages:
            if page_no < 1 or page_no > total:
                return (f"Page {page_no} doesn't exist — '{spec}' was "
                        f"checked against the real document, which has "
                        f"{total} page(s). Nothing was written.")
            selected.add(page_no - 1)
    if not selected:
        return (f"That page spec ('{spec}') covers no pages of this "
                f"{total}-page document. Nothing was written.")
    return selected


def _page_list_text(pages_1based: list[int]) -> str:
    """Compact a sorted 1-based page list: [1, 3, 4, 5, 8] → '1, 3-5, 8'."""
    if not pages_1based:
        return ""
    parts: list[str] = []
    start = previous = pages_1based[0]
    for page_no in pages_1based[1:]:
        if page_no == previous + 1:
            previous = page_no
            continue
        parts.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page_no
    parts.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(parts)


def rotate_pdf(path: str, degrees: int = 90, pages: str = "all",
               output: str = "") -> str:
    """Rotate selected pages of a PDF by 90, 180 or 270 degrees."""
    target = _existing_file(path, "PDF file")
    if isinstance(target, str):
        return target
    try:
        degrees = int(degrees)
    except (TypeError, ValueError):
        return "Degrees must be one of 90, 180 or 270."
    if degrees not in (90, 180, 270):
        return (f"PDF pages rotate in right angles — degrees must be "
                f"90, 180 or 270 (got {degrees}). Nothing was written.")
    pypdf, err = _load_pypdf()
    if err:
        return err
    try:
        reader = pypdf.PdfReader(str(target))
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}' couldn't be read as a PDF: {e}"
    if reader.is_encrypted:
        return (f"'{target.name}' is encrypted (password-protected) — "
                "its pages can't be rotated without the password.")
    try:
        total = len(reader.pages)
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}'s pages couldn't be read: {e}"
    if total == 0:
        return f"'{target.name}' has no pages to rotate."
    selection = _parse_page_selection(pages, total)
    if isinstance(selection, str):
        return selection
    if output and str(output).strip():
        dest = Path(str(output)).expanduser()
    else:
        dest = target.with_name(f"{target.stem}_rotated.pdf")
    final = _non_clobbering(dest)
    before = []
    for page in reader.pages:
        try:
            before.append(int(page.rotation) % 360)
        except Exception:  # noqa: BLE001
            before.append(0)
    try:
        writer = pypdf.PdfWriter()
        for index, page in enumerate(reader.pages):
            if index in selection:
                page.rotate(degrees)
            # Unselected pages go in as the very same page objects —
            # untouched, exactly as they were stored.
            writer.add_page(page)
        with open(final, "wb") as handle:
            writer.write(handle)
        writer.close()
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()  # never leave a half-written PDF behind
        except OSError:
            pass
        return f"The rotation couldn't be written: {e}"
    # Read-back verification: re-open the saved file and check
    # every page's rotation against what was asked for.
    verify = ""
    try:
        check_reader = pypdf.PdfReader(str(final))
        mismatch = None
        for index, page in enumerate(check_reader.pages):
            expected = (before[index]
                        + (degrees if index in selection else 0)) % 360
            if int(page.rotation) % 360 != expected:
                mismatch = index + 1
                break
        if mismatch is None:
            verify = (" Verified by re-reading the saved file: "
                      "every page's rotation matches the request.")
        else:
            verify = (f" Note: re-reading the saved file found "
                      f"page {mismatch}'s rotation doesn't match "
                      "the request — check it before relying on it.")
    except Exception:  # noqa: BLE001
        verify = (" (The saved file couldn't be re-opened to "
                  "verify its rotations.)")
    if len(selection) == total:
        scope = f"all {total} page{'s' if total != 1 else ''}"
    else:
        scope = _page_list_text(sorted(index + 1 for index in selection))
    return (f"Rotated {len(selection)} of {total} page"
            f"{'s' if total != 1 else ''} by {degrees}° in "
            f"'{target.name}' (pages: {scope}) → saved to {final} "
            f"({_human_size(final.stat().st_size)}). The original is "
            f"untouched.{_suffix_note(dest, final)}{verify}")
