import os
import re
import zipfile
from datetime import datetime
from pathlib import Path
# INTENTIONAL: cross-part names imported from the helpers module
# (bare-name rule, §2 P3) — these resolve in the defining
# module's globals, not the facade's.
from .skills_files5_helpers import (
    _COOK_SCAN_CAP,
    _IMAGE_EXTS,
    _existing_file,
    _existing_folder,
    _human_size,
    _load_cv2,
    _load_pil,
    _non_clobbering,
    _suffix_note,
)


# ---------------------------------------------------------------------------
# FOLDER WATCH COOK — arm a folder + recipe; run the recipe on demand
# ---------------------------------------------------------------------------

_RECIPES = ("rename_by_date", "move_pdfs", "zip_folder")
_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}_")
_WATCHES: dict[str, str] = {}   # resolved folder path -> recipe name


def _cook_rename_by_date(root: Path, cancel_token=None) -> list[str]:
    """Prefix each file with its modified date (YYYY-MM-DD_).

    B20: ``cancel_token`` is checked per file — a Stop pressed
    mid-cook raises ToolCancelled (caught by _run_recipe,
    which returns the cancelled dict). Renames are atomic
    per file, so already-renamed files stand and the rest
    are untouched.
    """
    from .funnel.tool_cancel import ToolCancelled

    changes = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if len(changes) >= _COOK_SCAN_CAP:
            break
        if cancel_token is not None and cancel_token.cancelled:
            raise ToolCancelled(
                f"cancelled after {len(changes)} rename(s)")
        try:
            if not entry.is_file() or entry.is_symlink():
                continue
            if _DATE_PREFIX.match(entry.name):
                continue
            stamp = datetime.fromtimestamp(
                entry.stat().st_mtime).strftime("%Y-%m-%d")
            dest = entry.with_name(f"{stamp}_{entry.name}")
            final = _non_clobbering(dest)
            os.rename(entry, final)
            if final.exists():
                changes.append(f"renamed '{entry.name}' → '{final.name}'")
        except OSError:
            continue
    return changes


def _cook_move_pdfs(root: Path, cancel_token=None) -> list[str]:
    """Move the folder's PDFs into a 'PDFs' subfolder.

    B20: ``cancel_token`` is checked per file (see
    _cook_rename_by_date — same contract: atomic moves,
    ToolCancelled on a stop).
    """
    from .funnel.tool_cancel import ToolCancelled

    pdf_dir = root / "PDFs"
    changes = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if len(changes) >= _COOK_SCAN_CAP:
            break
        if cancel_token is not None and cancel_token.cancelled:
            raise ToolCancelled(
                f"cancelled after {len(changes)} move(s)")
        try:
            if (not entry.is_file() or entry.is_symlink()
                    or entry.suffix.lower() != ".pdf"):
                continue
            pdf_dir.mkdir(parents=True, exist_ok=True)
            final = _non_clobbering(pdf_dir / entry.name)
            os.rename(entry, final)
            if final.exists():
                changes.append(f"moved '{entry.name}' → 'PDFs/{final.name}'")
        except OSError:
            continue
    return changes


def _cook_zip_folder(root: Path, cancel_token=None) -> list[str]:
    """Zip the folder's top-level files into '<folder>_files.zip'.

    B20: ``cancel_token`` is checked per member — a Stop
    pressed mid-archive raises ToolCancelled (see
    _cook_rename_by_date). The partial archive stays and is
    named (never deleted — it may be a pre-existing file's
    non-clobbering sibling, and deleting is the one
    irreversible act here).
    """
    from .funnel.tool_cancel import ToolCancelled

    dest = root / f"{root.name}_files.zip"
    final = _non_clobbering(dest)
    members = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if len(members) >= _COOK_SCAN_CAP:
            break
        try:
            if not entry.is_file() or entry.is_symlink():
                continue
            # Never swallow this recipe's own earlier archives.
            if entry.name == dest.name or (
                    entry.suffix.lower() == ".zip"
                    and entry.stem.startswith(dest.stem)):
                continue
            members.append(entry)
        except OSError:
            continue
    if not members:
        return []
    with zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in members:
            if cancel_token is not None and cancel_token.cancelled:
                raise ToolCancelled(
                    f"cancelled while zipping into '{final.name}'")
            archive.write(entry, arcname=entry.name)
    # Verify after the act: the archive must re-open with every member.
    with zipfile.ZipFile(final) as archive:
        inside = archive.namelist()
    return [f"zipped {len(inside)} file(s) into '{final.name}' "
            f"({_human_size(final.stat().st_size)})"]


_COOKERS = {
    "rename_by_date": _cook_rename_by_date,
    "move_pdfs": _cook_move_pdfs,
    "zip_folder": _cook_zip_folder,
}


def _run_recipe(root: Path, recipe: str, cancel_token=None) -> str:
    """Run one recipe on one folder and report what ACTUALLY changed.

    B20: a cooker stopped by the token raises ToolCancelled —
    caught here (before the generic Exception) and returned as
    the cancelled DICT (a string return would wrap as success).
    The already-applied changes stand (atomic per file) and are
    named in the message; the rest never ran.
    """
    from .funnel.tool_cancel import ToolCancelled
    from .funnel.tool_result import cancelled_result

    try:
        changes = _COOKERS[recipe](root, cancel_token=cancel_token)
    except ToolCancelled as exc:
        return cancelled_result(
            f"The '{recipe}' recipe on '{root}' was cancelled at "
            f"the user's request ({exc}) — the changes already "
            "applied stand; the rest did not run.")
    except Exception as e:  # noqa: BLE001
        return f"The '{recipe}' recipe failed on '{root}': {e}"
    if not changes:
        return (f"Ran '{recipe}' on '{root}' — nothing needed changing "
                "(no files matched, or everything was already in place).")
    shown = changes[:20]
    extra = (f" …and {len(changes) - len(shown)} more change(s)"
             if len(changes) > len(shown) else "")
    return (f"Ran '{recipe}' on '{root}' — {len(changes)} change(s), "
            f"each verified on disk: " + "; ".join(shown) + extra + ".")


def folder_watch_cook(action: str, folder: str = "", recipe: str = "",
                      cancel_token=None) -> str:
    """Arm/disarm a folder recipe watch, report status, or cook now.

    B20: ``cancel_token`` is threaded to _run_recipe — a Stop
    pressed mid-cook ends it promptly and returns the cancelled
    DICT. When cooking every armed watch, a cancelled watch
    stops the run (the remaining watches are not cooked after
    a stop).
    """
    action = (action or "status").strip().lower()
    recipe = (recipe or "").strip().lower()
    if recipe and recipe not in _RECIPES:
        return (f"I don't have a '{recipe}' recipe — the recipes are: "
                "rename_by_date (prefix each file with its modified "
                "date), move_pdfs (file PDFs into a 'PDFs' subfolder) "
                "and zip_folder (zip the folder's files into one "
                "archive inside it).")
    if action == "status":
        if not _WATCHES:
            return ("No folder watches are armed. Arming one remembers "
                    "a folder + recipe for THIS session only — there "
                    "is no background daemon, nothing runs until you "
                    "say run_once.")
        lines = ["Armed folder watches (this session only — nothing "
                 "runs in the background):"]
        for watched, rec in sorted(_WATCHES.items()):
            lines.append(f"• {watched} → {rec}")
        return "\n".join(lines)
    if action == "disarm":
        if not folder or not str(folder).strip():
            if not _WATCHES:
                return "No folder watches are armed, so nothing was disarmed."
            _WATCHES.clear()
            return "Disarmed all folder watches for this session."
        key = str(Path(str(folder)).expanduser())
        gone = _WATCHES.pop(key, None)
        if gone is None:
            return f"'{key}' wasn't armed — nothing was disarmed."
        return f"Disarmed the '{gone}' watch on '{key}'."
    if action == "arm":
        root = _existing_folder(folder, "folder to watch")
        if isinstance(root, str):
            return root
        if not recipe:
            return ("Arming needs a recipe: rename_by_date, move_pdfs "
                    "or zip_folder.")
        _WATCHES[str(root)] = recipe
        return (f"Armed: '{root}' will be cooked with '{recipe}' when "
                "you say run_once. Honest limit: the watch lives in "
                "this session's memory only — no background daemon "
                "watches the folder between asks, and the watch is "
                "forgotten when VYUK closes.")
    if action == "run_once":
        if folder and str(folder).strip():
            root = _existing_folder(folder, "folder to cook")
            if isinstance(root, str):
                return root
            rec = recipe or _WATCHES.get(str(root), "")
            if not rec:
                return (f"No recipe given for '{root}' and no watch "
                        "is armed on it — name one of: "
                        "rename_by_date, move_pdfs, zip_folder.")
            return _run_recipe(root, rec, cancel_token=cancel_token)
        if not _WATCHES:
            return ("Nothing to cook — no folder was named and no "
                    "watches are armed.")
        # B20: a cancelled watch stops the run — the remaining
        # watches are not cooked after a stop.
        _results = []
        for watched, rec in sorted(_WATCHES.items()):
            _one = _run_recipe(Path(watched), rec,
                               cancel_token=cancel_token)
            if isinstance(_one, dict) \
                    and _one.get("status") == "cancelled":
                return _one
            _results.append(_one)
        return "\n".join(_results)
    return ("Tell me an action: 'arm' (remember a folder + recipe), "
            "'disarm', 'status', or 'run_once' (cook right now).")


# ---------------------------------------------------------------------------
# PHOTO FACE BLUR — OpenCV Haar faces, pixelated, always a NEW file
# ---------------------------------------------------------------------------


def _face_cascade(cv2):
    """The bundled frontal-face Haar cascade, or None if unavailable."""
    try:
        cascade_path = os.path.join(
            cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        cascade = cv2.CascadeClassifier(cascade_path)
        if cascade.empty():
            return None
        return cascade
    except Exception:  # noqa: BLE001
        return None


def _detect_faces(cv2, gray) -> list:
    """Face rectangles [(x, y, w, h), …] in a grayscale frame."""
    cascade = _face_cascade(cv2)
    if cascade is None:
        return []
    faces = cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    return [tuple(int(v) for v in face) for face in faces]


def _pixelate(cv2, region, blocks: int = 12):
    """Crush a region to a coarse mosaic with cv2 resizes."""
    height, width = region.shape[:2]
    small_w = max(1, min(blocks, width))
    small_h = max(1, min(blocks, height))
    tiny = cv2.resize(region, (small_w, small_h),
                      interpolation=cv2.INTER_LINEAR)
    return cv2.resize(tiny, (width, height),
                      interpolation=cv2.INTER_NEAREST)


def photo_face_blur(path: str, output_path: str = "") -> str:
    """Blur every detected face in a photo; save as a NEW file."""
    target = _existing_file(path, "photo file")
    if isinstance(target, str):
        return target
    if target.suffix.lower() not in _IMAGE_EXTS:
        return (f"'{target.name}' doesn't look like an image file "
                f"({target.suffix.lower() or 'no extension'}) — face "
                "blur works on PNG, JPEG, GIF, BMP, WEBP and TIFF photos.")
    cv2, err = _load_cv2()
    if err:
        return err
    try:
        frame = cv2.imread(str(target))
    except Exception as e:  # noqa: BLE001
        return f"'{target.name}' couldn't be read as an image: {e}"
    if frame is None:
        return (f"'{target.name}' couldn't be read as an image — it "
                "may be corrupt or in a format OpenCV doesn't support.")
    if _face_cascade(cv2) is None:
        return ("OpenCV is installed but its bundled face-detection "
                "data (haarcascade_frontalface_default.xml) couldn't "
                "be loaded, so I can't look for faces.")
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _detect_faces(cv2, gray)
    except Exception as e:  # noqa: BLE001
        return f"Face detection failed on '{target.name}': {e}"
    if not faces:
        return (f"No faces detected in '{target.name}' — nothing was "
                "blurred and no copy was saved. (The detector looks "
                "for clear, front-facing faces; profiles, tiny or "
                "partly-hidden faces can be missed.)")
    blurred = frame.copy()
    for (x, y, w, h) in faces:
        region = blurred[y:y + h, x:x + w]
        if region.size:
            blurred[y:y + h, x:x + w] = _pixelate(cv2, region)
    if output_path and str(output_path).strip():
        dest = Path(str(output_path)).expanduser()
    else:
        dest = target.with_name(
            f"{target.stem}_faces_blurred{target.suffix}")
    if dest.resolve() == target.resolve():
        dest = target.with_name(
            f"{target.stem}_faces_blurred{target.suffix}")
    final = _non_clobbering(dest)
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(final), blurred):
            return "The blurred copy couldn't be written (OpenCV refused)."
    except Exception as e:  # noqa: BLE001
        return f"The blurred copy couldn't be written: {e}"
    if not final.exists() or final.stat().st_size == 0:
        return "The blurred copy was written but can't be found on disk — treating that as a failure."
    return (f"Blurred {len(faces)} face(s) in '{target.name}' "
            f"(pixelated) and saved the copy as {final} "
            f"({_human_size(final.stat().st_size)}). The original is "
            f"untouched.{_suffix_note(dest, final)} Honest limit: the "
            "detector finds clear front-facing faces — check the copy "
            "before sharing if a face was turned away or partly hidden.")


# ---------------------------------------------------------------------------
# SCREENSHOT TO TEXT NOTE — OCR a screenshot, keep the words as a note
# ---------------------------------------------------------------------------


def _ocr_image_text(target: Path) -> tuple[str | None, str | None]:
    """(text, None) or (None, honest error) — the pytesseract seam,
    mirroring skills_media.ocr_file's wording and lazy imports."""
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return None, ("Text reading isn't available in this build of "
                      "VYUK — the OCR part didn't make it into "
                      "the package.")
    except Exception as e:  # noqa: BLE001
        return None, (f"Text reading couldn't start: the OCR library "
                      f"failed to load ({e}).")
    Image, err = _load_pil()
    if err:
        return None, err
    try:
        import pytesseract

        with Image.open(target) as image:
            text = pytesseract.image_to_string(image).strip()
    except Exception as e:  # noqa: BLE001
        return None, f"OCR failed: {e}"
    return text, None


def screenshot_to_text_note(path: str) -> str:
    """Read a screenshot's text and save it as a dated note beside it.

    B5: the local Granite-Docling structured read first when its
    weights are present; Tesseract OCR otherwise (and when the
    Granite read fails or yields nothing). The note names its
    source either way.
    """
    target = _existing_file(path, "screenshot file")
    if isinstance(target, str):
        return target
    if target.suffix.lower() not in _IMAGE_EXTS:
        return (f"'{target.name}' doesn't look like an image file "
                f"({target.suffix.lower() or 'no extension'}) — OCR "
                "notes are made from PNG, JPEG, GIF, BMP, WEBP or "
                "TIFF screenshots.")
    text = None
    source = None
    try:
        from .vision_backend import read_page as _vb_read_page

        result = _vb_read_page(target.read_bytes())
        if result.get("ok") and (result.get("text") or "").strip():
            text = result["text"].strip()
            source = "Granite-Docling structured read"
    except Exception:  # noqa: BLE001 — Granite failure = OCR path
        pass
    if text is None:
        text, err = _ocr_image_text(target)
        if err:
            return err
        if not text:
            return (f"OCR ran on '{target.name}', but no readable text "
                    "was found in it — no note was saved.")
        source = "Tesseract OCR"
    stamp = datetime.now().strftime("%Y-%m-%d")
    dest = target.with_name(f"{target.stem}_text_{stamp}.txt")
    final = _non_clobbering(dest)
    note = (f"Text read from {target.name} ({source}) on "
            f"{datetime.now().strftime('%d %B %Y, %H:%M')}:\n\n{text}\n")
    try:
        final.write_text(note, encoding="utf-8")
    except OSError as e:
        return f"The note couldn't be written: {e}"
    if not final.exists():
        return "The note was written but can't be found on disk — treating that as a failure."
    preview = " ".join(text.split())
    if len(preview) > 80:
        preview = preview[:80] + "…"
    return (f"Read {len(text)} character(s) of text from "
            f"'{target.name}' ({source}) and saved the note as {final}. "
            f"It starts: \"{preview}\""
            f"{_suffix_note(dest, final)}")
