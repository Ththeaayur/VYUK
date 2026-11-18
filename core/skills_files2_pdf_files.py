# Skills split (Phase A): pdf-files tool implementations moved verbatim from core/skills_files_ops.py.
import os
import shutil
import struct
import subprocess
from pathlib import Path
# INTENTIONAL (skills split, bare-name rule): helper names used by these tools are imported from the helpers module.
from .skills_files2_helpers import (
    _is_windows,
    _human_size,
    _existing_file,
    _non_clobbering,
    _load_pypdf,
    _load_pil,
)


# ---------------------------------------------------------------------------
# MERGE PDFS
# ---------------------------------------------------------------------------


def _parse_pdf_paths(paths) -> tuple[list[str], bool]:
    """Normalise the merge input into a clean list of path strings.

    The schema promises an array, but the brains occasionally hand
    over one string of newline/comma-separated paths instead — split
    those defensively and report (via the bool) that we did, so the
    caller can say so in the result instead of hiding the guess."""
    if isinstance(paths, str):
        parts = []
        for chunk in paths.replace(",", "\n").splitlines():
            chunk = chunk.strip()
            if chunk:
                parts.append(chunk)
        return parts, True
    if isinstance(paths, (list, tuple)):
        return [str(p).strip() for p in paths if str(p).strip()], False
    return [], False


def merge_pdfs(paths, output: str = "") -> str:
    """Merge 2+ PDF files into one, in the given order.

    Every input is opened and page-counted BEFORE anything is
    written, so a missing or unreadable input aborts the merge
    naming that file, with no partial output left behind. The
    output is never overwritten: if the target name is taken, a
    " (2)"-style suffix is used and the result says the real name.
    """
    raw_paths, reinterpreted = _parse_pdf_paths(paths)
    if len(raw_paths) < 2:
        return ("Merging needs at least two PDF files — give their paths "
                "as a list, in the order they should appear.")
    targets: list[Path] = []
    for raw in raw_paths:
        found = _existing_file(raw, "PDF file")
        if isinstance(found, str):
            return f"Merge stopped before anything was written — {found}"
        targets.append(found)
    pypdf, err = _load_pypdf()
    if err:
        return err
    # Readability pass: open everything, count pages, THEN write.
    readers = []
    page_total = 0
    for target in targets:
        try:
            reader = pypdf.PdfReader(str(target))
            page_total += len(reader.pages)
            readers.append(reader)
        except Exception as e:  # noqa: BLE001
            for opened in readers:
                try:
                    opened.stream.close()
                except Exception:  # noqa: BLE001
                    pass
            return (f"Merge stopped before anything was written — "
                    f"'{target.name}' couldn't be read as a PDF: {e}")
    if output and str(output).strip():
        dest = Path(str(output)).expanduser()
    else:
        dest = targets[0].parent / "merged.pdf"
    final = _non_clobbering(dest)
    try:
        writer = pypdf.PdfWriter()
        for reader in readers:
            writer.append(reader)
        with open(final, "wb") as handle:
            writer.write(handle)
        writer.close()
    except Exception as e:  # noqa: BLE001
        try:
            if final.exists():
                final.unlink()  # never leave a half-written merge behind
        except OSError:
            pass
        return f"The PDFs were readable but the merge couldn't be written: {e}"
    finally:
        for reader in readers:
            try:
                reader.stream.close()
            except Exception:  # noqa: BLE001
                pass
    # Read-back verification: re-open what actually landed on disk
    # and confirm its page count survived the round trip.
    verify = ""
    try:
        check_reader = pypdf.PdfReader(str(final))
        written_pages = len(check_reader.pages)
        try:
            check_reader.stream.close()
        except Exception:  # noqa: BLE001
            pass
        if written_pages == page_total:
            verify = (f" Verified by re-opening the merged file: "
                      f"{written_pages} pages, "
                      f"{_human_size(final.stat().st_size)} on disk.")
        else:
            verify = (f" Note: re-opening the merged file counts "
                      f"{written_pages} pages, not the {page_total} "
                      "the inputs held — check it before relying on it.")
    except Exception as e:  # noqa: BLE001
        verify = (f" (The merged file couldn't be re-opened to "
                  f"verify it: {e})")
    names = ", ".join(t.name for t in targets)
    note = (" (Split the single string you gave into separate paths.)"
            if reinterpreted else "")
    suffix_note = (f" '{dest.name}' already existed, so the suffixed "
                   "name was used." if final != dest else "")
    return (f"Merged {len(targets)} PDFs ({page_total} pages) into {final}: "
            f"{names}.{suffix_note}{note}{verify}")


# ---------------------------------------------------------------------------
# IMAGE INFO
# ---------------------------------------------------------------------------


def image_info(path: str) -> str:
    """Report an image's facts: format, oriented size, mode, file
    size, and EXIF highlights (camera, date, GPS presence only)."""
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
                "The file may be corrupt or in a format Pillow doesn't support.")
    file_size = target.stat().st_size
    fmt = image.format or "unknown format"
    stored_w, stored_h = image.size
    # EXIF first (the stored image still carries its orientation tag).
    exif_lines: list[str] = []
    orientation = None
    try:
        exif = image.getexif()
    except Exception:  # noqa: BLE001
        exif = None
    if exif:
        orientation = exif.get(274)
        make = str(exif.get(271, "")).strip()
        model = str(exif.get(272, "")).strip()
        if model and make and model.lower().startswith(make.lower()):
            camera = model
        else:
            camera = " ".join(part for part in (make, model) if part)
        if camera:
            exif_lines.append(f"Camera: {camera}")
        taken = str(exif.get(306, "")).strip()
        if not taken:
            try:
                exif_ifd = exif.get_ifd(0x8769)
                taken = str(exif_ifd.get(36867, "")).strip()
            except Exception:  # noqa: BLE001
                taken = ""
        if taken:
            exif_lines.append(f"Date taken: {taken}")
        gps_present = False
        try:
            gps_present = bool(exif.get_ifd(0x8825))
        except Exception:  # noqa: BLE001
            gps_present = False
        # Presence only — coordinates are never read into the result.
        exif_lines.append(f"GPS data: {'present' if gps_present else 'absent'}")
        # Exposure settings sit in the EXIF sub-IFD next to the
        # date — surface whichever of them the camera recorded.
        try:
            settings_ifd = exif.get_ifd(0x8769)
        except Exception:  # noqa: BLE001
            settings_ifd = None
        if settings_ifd:
            bits: list[str] = []
            shutter = settings_ifd.get(33434)
            if shutter:
                try:
                    secs = float(shutter)
                    if 0 < secs < 1:
                        bits.append(f"1/{round(1 / secs)} s")
                    elif secs >= 1:
                        bits.append(f"{secs:g} s")
                except (TypeError, ValueError):
                    pass
            aperture = settings_ifd.get(33437)
            if aperture:
                try:
                    bits.append(f"f/{float(aperture):g}")
                except (TypeError, ValueError):
                    pass
            iso = settings_ifd.get(34855)
            if iso:
                try:
                    bits.append(f"ISO {int(iso)}")
                except (TypeError, ValueError):
                    pass
            focal = settings_ifd.get(37386)
            if focal:
                try:
                    bits.append(f"{float(focal):g} mm")
                except (TypeError, ValueError):
                    pass
            if bits:
                exif_lines.append("Exposure: " + " · ".join(bits))
    # Oriented dimensions: the size the picture is actually displayed
    # at (the v4.7 rule — geometry follows the EXIF orientation).
    try:
        from PIL import ImageOps

        shown_w, shown_h = ImageOps.exif_transpose(image).size
    except Exception:  # noqa: BLE001
        shown_w, shown_h = stored_w, stored_h
    lines = [
        f"'{target.name}': {fmt}, {shown_w}×{shown_h} pixels, "
        f"mode {image.mode}, {_human_size(file_size)} on disk."
    ]
    if (shown_w, shown_h) != (stored_w, stored_h):
        lines.append(
            f"Stored pixels are {stored_w}×{stored_h}; the EXIF orientation "
            f"tag ({orientation}) rotates it, so the displayed size above "
            "is the true one.")
    elif orientation not in (None, 1, "1"):
        lines.append(f"EXIF orientation tag: {orientation} (no rotation).")
    if exif_lines:
        lines.extend(exif_lines)
    else:
        lines.append("No EXIF metadata in this file.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SHRED FILE
# ---------------------------------------------------------------------------

_SHRED_MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB
_SHRED_CHUNK = 1024 * 1024  # 1 MiB


def shred_file(path: str, passes: int = 1) -> str:
    """Securely delete ONE file: overwrite with random bytes, then unlink."""
    if not path or not str(path).strip():
        return "Shredding needs the path of one file."
    target = Path(str(path)).expanduser()
    if not target.exists():
        return f"File not found: {target}"
    if target.is_dir():
        return (f"That's a folder, not a file: {target}. Shredding works "
                "on one file at a time — folders are refused so a wrong "
                "path can never wipe a whole tree.")
    if not target.is_file():
        return f"That's not a regular file, so it can't be shredded: {target}"
    size = target.stat().st_size
    if size > _SHRED_MAX_BYTES:
        return (f"'{target.name}' is {_human_size(size)} — shredding refuses "
                "files over 2 GB, because overwriting gigabytes of random "
                "data is slow, wears the drive, and a file that big is "
                "almost never the intended target. Split or delete it "
                "normally instead.")
    try:
        passes = int(passes)
    except (TypeError, ValueError):
        return "Passes must be a whole number (1–3)."
    passes = max(1, min(3, passes))
    try:
        with open(target, "r+b") as handle:
            for _ in range(passes):
                handle.seek(0)
                remaining = size
                while remaining > 0:
                    chunk = os.urandom(min(_SHRED_CHUNK, remaining))
                    handle.write(chunk)
                    remaining -= len(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            handle.truncate(0)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as e:
        return (f"Could not fully shred '{target.name}': {e}. The file "
                "may be partly overwritten — check it before trusting "
                "that its contents are gone.")
    try:
        os.unlink(target)
    except OSError as e:
        return (f"'{target.name}' was overwritten and emptied, but couldn't "
                f"be unlinked: {e}")
    # R1a: verify the shred — the file must be gone. An unlink
    # that returned quietly is the only proof the code had before;
    # the absence check is the world confirming it.
    from . import tool_outcome as _outcome  # R1a: seam wiring

    ok, evidence = _outcome.file_absent(target)
    message = (f"Shredded '{target.name}': {size} bytes overwritten with random "
               f"data ({passes} pass{'es' if passes > 1 else ''}), truncated "
               "and deleted.")
    if ok:
        return _outcome.verified(message, evidence)
    return _outcome.failed(
        f"'{target.name}' was overwritten but could not be confirmed "
        f"deleted: {evidence}.", evidence)


# ---------------------------------------------------------------------------
# SYNC FOLDER
# ---------------------------------------------------------------------------

_SYNC_MAX_FILES = 5000


def sync_folder(source: str, destination: str, mirror_delete: bool = False,
                dry_run: bool = False, cancel_token=None) -> str:
    """One-way sync: copy new/changed files source → destination.

    dry_run=true walks and compares everything but writes nothing:
    the result reports what WOULD be copied (and, with
    mirror_delete, what would be deleted) instead. A real (non-dry)
    overwrite never destroys the destination's previous version:
    it is rescued into VYUK's central trash folder first, via
    core/desktop/control.py's shared _rescue_overwrite_victim (v5.44,
    H7b — the H7 rule file_control already followed; the rescue
    destination became the one central trash root in v5.48,
    programme item 27 — it was a sibling _nova_trash folder
    before that).

    R1a (tools loopholes, seam pass 2): copied files are verified
    by byte comparison and mirror-deleted files by absence; the
    result is an Outcome (a dry run changes nothing, so it stays
    a plain string). Refusal returns stay plain strings.

    B20: ``cancel_token`` is checked per file copied and per
    mirror delete — a Stop pressed mid-sync ends it promptly
    and returns the cancelled DICT (a string return would wrap
    as success; an Outcome would read as verified). Copied
    files are complete and verified (the byte check already
    ran per file); mirror deletes never run after a stop.
    """
    from . import tool_outcome as _outcome  # R1a: seam wiring

    if not source or not str(source).strip():
        return "Sync needs a source folder."
    if not destination or not str(destination).strip():
        return "Sync needs a destination folder."
    src = Path(str(source)).expanduser()
    dst = Path(str(destination)).expanduser()
    if not src.exists():
        return f"Source folder not found: {src}"
    if not src.is_dir():
        return f"The source is a file, not a folder: {src}"
    src_resolved = src.resolve()
    dst_resolved = dst.resolve()
    if src_resolved == dst_resolved:
        return ("Source and destination are the same folder — a sync "
                "onto itself would do nothing at best. Pick two "
                "different folders.")
    if dst_resolved.is_relative_to(src_resolved):
        return ("The destination is INSIDE the source folder — syncing "
                "there would copy the copy into itself forever. Put "
                "the destination somewhere else.")
    if src_resolved.is_relative_to(dst_resolved):
        return ("The source is INSIDE the destination folder — with "
                "mirroring on, the sync could delete the very files "
                "it's copying from. Pick two separate folders.")
    if dst.exists() and not dst.is_dir():
        return f"The destination exists but is a file, not a folder: {dst}"
    if not dry_run:
        try:
            dst.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"Could not create the destination folder {dst}: {e}"

    # Inventory the source: relpath -> (size, mtime_ns). Symlinks are
    # skipped and counted, never followed.
    src_files: dict[str, Path] = {}
    symlinks_skipped = 0
    cap_hit = False
    for dirpath, dirnames, filenames in os.walk(src):
        current = Path(dirpath)
        kept_dirs = []
        for dirname in dirnames:
            if (current / dirname).is_symlink():
                symlinks_skipped += 1
            else:
                kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            if len(src_files) >= _SYNC_MAX_FILES:
                cap_hit = True
                break
            item = current / filename
            if item.is_symlink():
                symlinks_skipped += 1
                continue
            src_files[item.relative_to(src).as_posix()] = item
        if cap_hit:
            dirnames[:] = []

    copied: list[str] = []
    copied_bytes = 0
    up_to_date = 0
    errors: list[str] = []
    rescued: list[Path] = []  # overwrite victims rescued first (H7b)
    for rel, item in src_files.items():
        # B20: the per-file yield point — a Stop pressed
        # mid-sync ends the loop here. Copied files are
        # complete and verified; nothing is half-copied (a
        # copy2 that never started leaves the destination
        # untouched, and an overwrite's victim was already
        # rescued). Mirror deletes never run after a stop.
        if cancel_token is not None and cancel_token.cancelled:
            from .funnel.tool_result import cancelled_result
            return cancelled_result(
                f"Syncing {src} → {dst} was cancelled at the user's "
                f"request after {len(copied)} file(s) copied and "
                f"{up_to_date} already up to date — the rest did "
                "not run and no mirror deletes ran.")
        dest_item = dst / rel
        try:
            if dest_item.is_file() and not dest_item.is_symlink():
                src_stat = item.stat()
                dst_stat = dest_item.stat()
                if (src_stat.st_size == dst_stat.st_size
                        and src_stat.st_mtime_ns == dst_stat.st_mtime_ns):
                    up_to_date += 1
                    continue
            if not dry_run:
                dest_item.parent.mkdir(parents=True, exist_ok=True)
                if dest_item.is_file() or dest_item.is_symlink():
                    # v5.44 (H7b): an overwrite here used to destroy
                    # the destination in place via copy2. Rescue the
                    # victim FIRST through the SAME helper
                    # file_control uses (core/desktop/control.py's
                    # _rescue_overwrite_victim — central trash
                    # root since v5.48 item 27, timestamped,
                    # uniquified). A rescue that fails
                    # fails this item and leaves the victim untouched,
                    # exactly like file_control.
                    try:
                        from .desktop.control import _rescue_overwrite_victim  # Phase 3 D4a: control hub moved to core/desktop/

                        rescued.append(
                            _rescue_overwrite_victim(dest_item))
                    except Exception as e:  # noqa: BLE001 — per-item
                        errors.append(
                            f"{rel}: could not rescue the existing "
                            f"file before overwriting it ({e})")
                        continue
                shutil.copy2(item, dest_item)
                # R1a: verify the copy by byte comparison — a copy
                # whose bytes differ is an error, not a copied file.
                _ok, _evidence = _outcome.file_copied(item, dest_item)
                if not _ok:
                    errors.append(
                        f"{rel}: verification failed: {_evidence}")
                    continue
            copied.append(rel)
            try:
                copied_bytes += item.stat().st_size
            except OSError:
                pass
        except OSError as e:
            errors.append(f"{rel}: {e}")

    # Destination extras: files the source doesn't have.
    extras: list[str] = []
    dest_cap_hit = False
    dest_count = 0
    for dirpath, dirnames, filenames in os.walk(dst):
        current = Path(dirpath)
        # A legacy per-folder _nova_trash holds rescued overwrite
        # victims from before v5.48 (H7b): it is recovery storage,
        # never a sync "extra" — and with mirror_delete on, listing
        # it here would delete the very files a rescue saved.
        # (v5.48 item 27 moved new rescues to the central root,
        # which never sits inside a sync destination at all.)
        dirnames[:] = [d for d in dirnames
                       if d != "_nova_trash"
                       and not (current / d).is_symlink()]
        for filename in filenames:
            dest_count += 1
            if dest_count > _SYNC_MAX_FILES:
                dest_cap_hit = True
                break
            rel = (current / filename).relative_to(dst).as_posix()
            if rel not in src_files:
                extras.append(rel)
        if dest_cap_hit:
            dirnames[:] = []

    deleted = 0
    dirs_removed = 0
    if mirror_delete and extras:
        for rel in extras:
            # B20: the per-delete yield point — a Stop pressed
            # mid-delete ends the loop here; deleted files are
            # gone (verified absent per file), the rest stay.
            if cancel_token is not None and cancel_token.cancelled:
                from .funnel.tool_result import cancelled_result
                return cancelled_result(
                    f"Syncing {src} → {dst} was cancelled at the user's "
                    f"request after {len(copied)} file(s) copied and "
                    f"{deleted} destination file(s) mirror-deleted — "
                    "the rest did not run.")
            if dry_run:
                deleted += 1
                continue
            try:
                os.unlink(dst / rel)
                # R1a: verify the mirror delete — the file must be gone.
                _gone, _gone_reason = _outcome.file_absent(dst / rel)
                if not _gone:
                    errors.append(
                        f"delete {rel}: verification failed: {_gone_reason}")
                    continue
                deleted += 1
            except OSError as e:
                errors.append(f"delete {rel}: {e}")
        if not dry_run:
            for dirpath, dirnames, _filenames in os.walk(dst, topdown=False):
                current = Path(dirpath)
                if current == dst or current.name == "_nova_trash":
                    continue
                try:
                    if not os.listdir(current):
                        current.rmdir()
                        dirs_removed += 1
                except OSError:
                    pass

    if dry_run:
        lines = [
            f"Dry run — nothing was copied or deleted. Syncing "
            f"{src} → {dst} would copy {len(copied)} file(s); "
            f"{up_to_date} already up to date."
        ]
    else:
        lines = [
            f"Synced {src} → {dst}: {len(copied)} file(s) copied, "
            f"{up_to_date} already up to date."
        ]
    if copied:
        shown = ", ".join(copied[:5])
        more = f" (+{len(copied) - 5} more)" if len(copied) > 5 else ""
        lines.append(f"{'Would copy' if dry_run else 'Copied'}: {shown}{more}")
        lines.append(
            f"Total data {'to copy' if dry_run else 'copied'}: "
            f"{_human_size(copied_bytes)}.")
    if rescued:
        shown_rescue = ", ".join(str(p) for p in rescued[:3])
        more_rescue = (f" (+{len(rescued) - 3} more)"
                       if len(rescued) > 3 else "")
        lines.append(
            f"Replaced {len(rescued)} existing file(s) — the previous "
            f"version(s) were moved to the central trash folder "
            f"before being replaced, so they can be recovered: "
            f"{shown_rescue}{more_rescue}.")
    if mirror_delete:
        if deleted:
            lines.append(
                f"Mirror delete ON: "
                f"{'would remove' if dry_run else 'removed'} {deleted} "
                f"destination file(s) that the source doesn't have"
                + (f" and {dirs_removed} emptied folder(s)."
                   if dirs_removed else "."))
        else:
            lines.append("Mirror delete ON: no destination extras to remove.")
    elif extras:
        shown = ", ".join(extras[:5])
        more = f" (+{len(extras) - 5} more)" if len(extras) > 5 else ""
        lines.append(
            f"{len(extras)} destination file(s) aren't in the source "
            f"({shown}{more}) — left UNTOUCHED, because mirror_delete "
            "is off. Nothing is ever deleted unless mirror_delete=true.")
    if symlinks_skipped:
        lines.append(f"{symlinks_skipped} symlink(s) in the source were skipped, not followed.")
    if cap_hit:
        lines.append(
            f"Scan stopped at the {_SYNC_MAX_FILES}-file cap — files "
            "past it were not compared or copied.")
    if dest_cap_hit:
        lines.append(
            f"The destination scan also hit the {_SYNC_MAX_FILES}-file "
            "cap, so the extras list may be incomplete.")
    if errors:
        lines.append(f"{len(errors)} error(s): " + "; ".join(errors[:3]))
    result = "\n".join(lines)
    if dry_run:
        return result  # a dry run changes nothing — plain string
    # R1a: the sync result is an Outcome — verified only when every
    # copied file byte-matched and every mirror delete landed; any
    # error makes it a failed Outcome (the message already lists
    # the errors).
    if errors:
        return _outcome.failed(result, "%d error(s) during the sync"
                               % len(errors))
    return _outcome.verified(
        result,
        "%d file(s) copied and verified, %d already up to date"
        % (len(copied), up_to_date))


# ---------------------------------------------------------------------------
# COPY FILE TO CLIPBOARD
# ---------------------------------------------------------------------------

_CF_HDROP = 15
_DROPFILES_HEADER_SIZE = 20  # DWORD pFiles + POINT pt + BOOL fNC + BOOL fWide


def _dropfiles_blob(paths: list[str]) -> bytes:
    """Build the CF_HDROP payload for `paths` (pure, testable).

    Layout: a 20-byte DROPFILES header — pFiles (offset of the file
    list from the struct start) = 20, pt = (0, 0), fNC = FALSE,
    fWide = TRUE — then each path as UTF-16-LE with a NUL
    terminator, and one final extra NUL ending the list."""
    header = struct.pack(
        "<IiiII",
        _DROPFILES_HEADER_SIZE,  # pFiles
        0, 0,                    # pt.x, pt.y
        0,                       # fNC
        1,                       # fWide — the list is wide (UTF-16)
    )
    blob = bytearray(header)
    for path in paths:
        blob += path.encode("utf-16-le") + b"\x00\x00"
    blob += b"\x00\x00"
    return bytes(blob)


def _set_clipboard_files_windows(paths: list[str]) -> str | None:
    """Put files on the Windows clipboard as CF_HDROP.

    Returns None on success, or an error string."""
    import ctypes
    from ctypes import wintypes

    blob = _dropfiles_blob(paths)
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.restype = wintypes.LPVOID
    user32.SetClipboardData.restype = wintypes.HANDLE
    handle = kernel32.GlobalAlloc(0x0002, len(blob))  # GMEM_MOVEABLE
    if not handle:
        return "Windows couldn't allocate clipboard memory."
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        return "Windows couldn't lock clipboard memory."
    try:
        ctypes.memmove(pointer, blob, len(blob))
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        return "The clipboard is busy (another app is holding it) — try again."
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(_CF_HDROP, handle):
            kernel32.GlobalFree(handle)
            return "Windows refused the clipboard data."
        # Success: the clipboard now owns the memory — do NOT free it.
    finally:
        user32.CloseClipboard()
    return None


def _file_uri(path: Path) -> str:
    """file:// URI for a local path (for text/uri-list clipboards)."""
    from urllib.request import pathname2url

    return "file://" + pathname2url(str(path.resolve()))


def copy_file_to_clipboard(path: str):
    """Put a FILE on the clipboard (pasteable into Explorer/folders).

    R1a (tools loopholes, seam pass 2): the success claim is
    VERIFIED by reading the clipboard's file list back
    (core.tool_outcome.clipboard_files_equal) — a SetClipboardData
    that returned quietly is not proof the file is on the
    clipboard. The result is an Outcome; refusal returns stay
    plain strings.
    """
    from . import tool_outcome as _outcome  # R1a: seam wiring

    target = _existing_file(path)
    if isinstance(target, str):
        return target
    if _is_windows():
        error = _set_clipboard_files_windows([str(target)])
        if error:
            return f"Couldn't copy '{target.name}' to the clipboard: {error}"
        ok, evidence = _outcome.clipboard_files_equal(target)
        message = (f"'{target.name}' is on the clipboard as a file — paste "
                   "it into a folder (Explorer, Desktop) to drop a copy there.")
        if ok:
            return _outcome.verified(message, evidence)
        if "no files" in evidence or "holds " in evidence:
            return _outcome.failed(
                f"'{target.name}' did not stay on the clipboard: "
                f"{evidence}.", evidence)
        return _outcome.unverified(
            message + " (I couldn't read the clipboard's file list "
            "back to confirm it stayed.)", evidence)
    xclip = shutil.which("xclip")
    if xclip:
        try:
            completed = subprocess.run(
                [xclip, "-selection", "clipboard", "-t", "text/uri-list"],
                input=_file_uri(target) + "\n",
                capture_output=True, text=True, timeout=10,
            )
        except Exception as e:  # noqa: BLE001
            return f"Couldn't copy '{target.name}' to the clipboard: xclip failed: {e}"
        if completed.returncode == 0:
            ok, evidence = _outcome.clipboard_files_equal(target)
            message = (f"'{target.name}' is on the clipboard as a file "
                       "reference (text/uri-list) — paste it into a file "
                       "manager to copy it there.")
            if ok:
                return _outcome.verified(message, evidence)
            if "no files" in evidence or "holds " in evidence:
                return _outcome.failed(
                    f"'{target.name}' did not stay on the clipboard: "
                    f"{evidence}.", evidence)
            return _outcome.unverified(
                message + " (I couldn't read the clipboard's file "
                "list back to confirm it stayed.)", evidence)
        detail = (completed.stderr or "").strip()[:200]
        return (f"Couldn't copy '{target.name}' to the clipboard: xclip "
                f"reported an error{f' — {detail}' if detail else ''}.")
    return (f"Couldn't copy '{target.name}' to the clipboard: copying "
            "files (not text) needs Windows, or xclip on Linux — "
            "neither is available here, so nothing was copied.")
