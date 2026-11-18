"""R9-8 elodie port: EXIF photo organiser as own code (no adoption).

Elodie (jmathai/elodie, Apache-2.0, PyPI 0.0.1b0) sorts photos into
dated, location-aware folders and renames by metadata. It is NOT
adopted: its wheel pins 2017-2018 versions (click==6.6, Pillow==5.3.0,
requests==2.20.0) that directly conflict with the tree's Pillow 13 /
requests 2.34, and it is a beta. What ships here is elodie's
ALGORITHM, re-implemented on the tree's own EXIF reading (Pillow
``getexif``, already declared at requirements.txt:33 and already used
by ``image_info`` and ``_exif_summary``):

- Folder: ``<dest>/<YYYY>-<MM>-<Mon>/<location>/`` — elodie's default
  ``%Y-%m-%b`` folder, e.g. ``2015-01-Jan``.
- File: ``<YYYY>-<MM>-<DD>_<HH>-<MM>-<SS>-<original-stem>.<ext>`` —
  elodie's default ``%date-%original_name-%title.%extension`` with the
  title part omitted (titles come from elodie's plugins; none ship).
- Date: EXIF DateTimeOriginal (36867) -> DateTimeDigitized (36868) ->
  DateTime (306) -> file mtime fallback. DateTimeOriginal is the
  shutter moment, so it leads: 306 is the file's last-modification
  date and would misfile an edited photo. (Note: ``image_info``
  prefers 306 for its "Date taken" display; this module deliberately
  differs, because sorting a camera dump is about when the photo was
  taken, not when it was last edited.) A photo with no usable date
  at all goes to ``Unknown/`` with its name kept.
- Location: elodie reverse-geocodes GPS into a city name over the
  network. This port does NOT — no network, no geocoder. GPS photos
  get a COARSE coordinate bucket as the location folder,
  ``GPS-19.1N-72.9E`` (rounded to 0.1 degrees, about 11 km — coarse by
  design, so no precise position is written into a folder name);
  photos without GPS go to ``Unknown Location`` (elodie's own default
  string). Folders are local only, never sent anywhere.

DIVERGENCES from elodie, stated plainly:
- Photos only (the same _IMAGE_EXTS as duplicate_photo_finder).
  Videos are counted and left untouched: elodie reads video dates
  through exiftool/ffmpeg metadata and the tree has no video metadata
  reader. Saying so is better than guessing a video's date.
- Stateless: elodie keeps a hash db and a location db under ~/.elodie.
  This port keeps NO state file — a photo already at its organised
  destination with identical bytes is skipped as already organised,
  and a dry run changes nothing AND writes no state.
- No plugins, no titles, no album handling, no config file.

SAFETY (the move discipline):
- Confirm-gated funnel tool; dry_run defaults to TRUE, so a bare call
  only previews. A dry run never creates the destination, never moves
  a file, never writes state.
- Never overwrites: a taken destination name gets the photo-lab
  `` (2)``-style suffix and the result says so. An overwrite victim
  can therefore never occur — there is nothing to rescue.
- Every move is verified by byte comparison after the move
  (A4-filestat); a failed move or a failed verify is reported by
  name and the rest still run.
- The destination must not sit inside the source (it would be walked
  into itself) and the source and destination must differ.
- Never auto-organises: no watcher, no auto-start, no organising on
  import. The user names both folders, every time.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path

from .skills_files5_helpers import (
    _IMAGE_EXTS,
    _PHOTO_SCAN_CAP,
    _existing_folder,
    _load_pil,
    _non_clobbering,
    _suffix_note,
)

# Elodie's folder date mask, verbatim: %Y-%m-%b -> "2015-01-Jan".
_FOLDER_DATE_FORMAT = "%Y-%m-%b"
# Elodie's file date mask, verbatim: %Y-%m-%d_%H-%M-%S.
_FILE_DATE_FORMAT = "%Y-%m-%d_%H-%M-%S"
# EXIF date strings look like "2015:01:01 12:34:56".
_EXIF_DATE_FORMAT = "%Y:%m:%d %H:%M:%S"
# Location folders when the photo carries no GPS at all — elodie's
# own default string for the missing-location slot.
_UNKNOWN_LOCATION = "Unknown Location"
# Coarse GPS bucket: 0.1 degrees is about 11 km at the equator — a
# neighbourhood, never a precise position, by design.
_GPS_BUCKET_DEGREES = 0.1
# Video extensions, counted and left untouched (no video metadata
# reader in the tree, so a video's date would be a guess).
_VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp")
# Windows-illegal filename characters, replaced with "-" in stems.
_ILLEGAL_STEM_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitise_stem(stem: str) -> str:
    """Make a filename stem safe: no illegal characters, no trailing
    dots or spaces (Windows drops them silently)."""
    cleaned = _ILLEGAL_STEM_CHARS.sub("-", stem).strip().rstrip(". ")
    return cleaned or "photo"


def _parse_exif_datetime(value) -> datetime | None:
    """Parse an EXIF date string ("2015:01:01 12:34:56") or None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), _EXIF_DATE_FORMAT)
    except (ValueError, TypeError):
        return None


def _rational_to_float(value) -> float | None:
    """An EXIF rational (Pillow IFDRational, int, or float) as a float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dms_to_decimal(parts, ref: str) -> float | None:
    """GPS DMS triple (degrees, minutes, seconds) + N/S/E/W ref."""
    if not parts or len(parts) < 3:
        return None
    degrees = _rational_to_float(parts[0])
    minutes = _rational_to_float(parts[1])
    seconds = _rational_to_float(parts[2])
    if degrees is None or minutes is None or seconds is None:
        return None
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    if str(ref).strip().upper() in ("S", "W"):
        decimal = -decimal
    return decimal


def _read_photo_date(image, path: Path) -> tuple[datetime | None, str]:
    """(date taken, source-of-date). EXIF original -> digitized ->
    IFD0 date -> file mtime. Returns (None, "none") only when even
    the mtime cannot be read."""
    try:
        exif = image.getexif()
    except Exception:  # noqa: BLE001
        exif = None
    if exif:
        # DateTimeOriginal first: the shutter moment is what a
        # camera dump sorts by. 306 (DateTime) is the file's
        # last-modification date and would misfile an edited photo.
        try:
            sub = exif.get_ifd(0x8769)
        except Exception:  # noqa: BLE001
            sub = None
        taken = None
        if sub:
            taken = _parse_exif_datetime(sub.get(36867))
            if taken is None:
                taken = _parse_exif_datetime(sub.get(36868))
        if taken is None:
            taken = _parse_exif_datetime(exif.get(306))
        if taken is not None:
            return taken, "exif"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime), "mtime"
    except OSError:
        return None, "none"


def _read_gps(image) -> tuple[float, float] | None:
    """(latitude, longitude) in decimal degrees, or None when the
    photo carries no usable GPS block."""
    try:
        exif = image.getexif()
    except Exception:  # noqa: BLE001
        return None
    if not exif:
        return None
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:  # noqa: BLE001:
        return None
    if not gps:
        return None
    lat = _dms_to_decimal(gps.get(2), gps.get(1, "N"))
    lon = _dms_to_decimal(gps.get(4), gps.get(3, "E"))
    if lat is None or lon is None:
        return None
    return lat, lon


def _coarse_location(lat: float, lon: float) -> str:
    """Coarse GPS bucket, e.g. "GPS-19.1N-72.9E" — rounded to 0.1
    degrees, so the folder names a neighbourhood, never a position."""
    step = _GPS_BUCKET_DEGREES
    lat_b = round(lat / step) * step
    lon_b = round(lon / step) * step
    lat_s = f"{abs(lat_b):.1f}{'N' if lat_b >= 0 else 'S'}"
    lon_s = f"{abs(lon_b):.1f}{'E' if lon_b >= 0 else 'W'}"
    return f"GPS-{lat_s}-{lon_s}"


def _sha256(path: Path) -> str | None:
    """Hex digest of a file, or None when it cannot be read."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _plan_moves(source: Path, dest: Path):
    """Walk `source` and work out every move.

    Returns (moves, already, videos, unreadable, over_cap) where moves
    is a list of dicts {src, dest, date, date_source, location,
    note}; already is the count of photos already at their organised
    destination with identical bytes; videos is the count of video
    files left untouched; unreadable is the count of image files
    Pillow could not open; over_cap is True when the 1000-image cap
    cut the walk short."""
    Image, err = _load_pil()
    if err:
        return None, err
    try:
        all_files = sorted(
            (p for p in source.rglob("*") if p.is_file()),
            key=lambda p: str(p).lower(),
        )
    except OSError as exc:
        return None, f"Couldn't walk '{source}': {exc}"
    images = [p for p in all_files if p.suffix.lower() in _IMAGE_EXTS]
    videos = sum(1 for p in all_files if p.suffix.lower() in _VIDEO_EXTS)
    over_cap = len(images) > _PHOTO_SCAN_CAP
    images = images[:_PHOTO_SCAN_CAP]
    moves: list[dict] = []
    already = 0
    unreadable = 0
    for candidate in images:
        try:
            with Image.open(candidate) as image:
                image.load()
                taken, date_source = _read_photo_date(image, candidate)
                gps = _read_gps(image)
        except Exception:  # noqa: BLE001
            unreadable += 1
            continue
        if taken is None:
            folder = dest / "Unknown"
            location = _UNKNOWN_LOCATION
            file_date = "unknown-date"
        else:
            folder = dest / taken.strftime(_FOLDER_DATE_FORMAT)
            location = _coarse_location(*gps) if gps else _UNKNOWN_LOCATION
            file_date = taken.strftime(_FILE_DATE_FORMAT)
        folder = folder / location
        stem = _sanitise_stem(candidate.stem)
        wanted = folder / f"{file_date}-{stem}{candidate.suffix.lower()}"
        if taken is None:
            wanted = folder / f"{stem}{candidate.suffix.lower()}"
        if wanted.exists():
            src_hash = _sha256(candidate)
            if src_hash is not None and src_hash == _sha256(wanted):
                already += 1
                continue
        final = _non_clobbering(wanted)
        moves.append({
            "src": candidate,
            "dest": final,
            "date": file_date,
            "date_source": date_source,
            "location": location,
            "note": _suffix_note(wanted, final),
        })
    return (moves, already, videos, unreadable, over_cap), None


def _format_plan(source: Path, dest: Path, plan, dry_run: bool) -> str:
    """The plan as plain language: counts first, then the moves."""
    moves, already, videos, unreadable, over_cap = plan
    lines = []
    if dry_run:
        lines.append(
            f"Dry run — nothing moved, nothing created. {len(moves)} "
            f"photo(s) under '{source}' would move into '{dest}':")
    else:
        lines.append(
            f"Plan for '{source}' into '{dest}': {len(moves)} photo(s) "
            f"to move.")
    shown = moves[:10]
    for move in shown:
        lines.append(
            f"  {move['src'].name} -> "
            f"{move['dest'].relative_to(dest)}{move['note']}")
    if len(moves) > len(shown):
        lines.append(f"  ... and {len(moves) - len(shown)} more.")
    if already:
        lines.append(
            f"{already} photo(s) already sit at their organised "
            f"destination with identical bytes — left alone.")
    if videos:
        lines.append(
            f"{videos} video file(s) left untouched — videos are not "
            f"organised (no video metadata reader in the tree).")
    if unreadable:
        lines.append(
            f"{unreadable} image file(s) couldn't be opened — left "
            f"untouched.")
    if over_cap:
        lines.append(
            f"Only the first {_PHOTO_SCAN_CAP} images were planned — "
            f"the folder holds more; run again for the rest.")
    if not moves and not already and not videos and not unreadable:
        lines.append("No image files found under the source folder.")
    return "\n".join(lines)


def _validate(source: str, destination: str):
    """Resolve both folders, or return (None, None, error string).
    The destination may not exist yet (it is created on a real run);
    it must not sit inside the source, and the two must differ."""
    root = _existing_folder(source, "source photo folder")
    if isinstance(root, str):
        return None, None, root
    if not destination or not str(destination).strip():
        return None, None, "That needs the path of a destination folder."
    dest = Path(str(destination)).expanduser()
    try:
        root_resolved = root.resolve()
        dest_resolved = dest.resolve()
    except OSError:
        root_resolved = root
        dest_resolved = dest
    if dest_resolved == root_resolved:
        return None, None, (
            "The source and destination folders are the same — "
            "there is nothing to organise.")
    if dest_resolved.is_relative_to(root_resolved):
        return None, None, (
            "The destination sits inside the source folder — the "
            "organised photos would be walked into themselves. "
            "Pick a destination outside the source.")
    return root, dest, None


def preview_photo_organisation(source: str, destination: str,
                               cancel_token=None) -> str:
    """Show exactly what organising would do, without moving, creating
    or writing anything. Pure read — the dry-run plan itself."""
    _root, _dest, error = _validate(source, destination)
    if error:
        return error
    root, dest = _root, _dest
    plan, error = _plan_moves(root, dest)
    if error:
        return error
    return _format_plan(root, dest, plan, dry_run=True)


def organise_photos(source: str, destination: str, dry_run: bool = True,
                    cancel_token=None) -> str:
    """Sort a camera dump into dated, location-aware folders (elodie's
    scheme, own code). dry_run=true (default) only previews — nothing
    moves, nothing is created. dry_run=false moves each photo to
    <destination>/<YYYY>-<MM>-<Mon>/<location>/<YYYY>-<MM>-<DD>_<HH>-<MM>-<SS>-<name>.<ext>,
    never overwriting, verifying every move by byte comparison."""
    root, dest, error = _validate(source, destination)
    if error:
        return error
    plan, error = _plan_moves(root, dest)
    if error:
        return error
    if dry_run:
        return _format_plan(root, dest, plan, dry_run=True)
    moves, already, videos, unreadable, over_cap = plan
    if not moves:
        return _format_plan(root, dest, plan, dry_run=False)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Couldn't create the destination folder '{dest}': {exc}"
    moved = 0
    suffix_notes: list[str] = []
    failed: list[str] = []
    for move in moves:
        if cancel_token is not None and cancel_token():
            failed.append(f"{move['src'].name} (cancelled)")
            break
        src_hash = _sha256(move["src"])
        try:
            move["dest"].parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(move["src"]), str(move["dest"]))
        except OSError as exc:
            failed.append(f"{move['src'].name} (couldn't move: {exc})")
            continue
        if not move["dest"].exists():
            failed.append(
                f"{move['src'].name} (the destination file is missing "
                f"after the move)")
            continue
        if src_hash is not None and _sha256(move["dest"]) != src_hash:
            failed.append(
                f"{move['src'].name} (the moved file's bytes don't "
                f"match the original)")
            continue
        moved += 1
        if move["note"]:
            suffix_notes.append(move["note"])
    lines = [f"Moved {moved} photo(s) from '{root}' into '{dest}'."]
    if suffix_notes:
        lines.append(
            f"{len(suffix_notes)} file(s) used a suffixed name:")
        for note in suffix_notes[:10]:
            lines.append(f"  {note.strip()}")
        if len(suffix_notes) > 10:
            lines.append(f"  ... and {len(suffix_notes) - 10} more.")
    if already:
        lines.append(
            f"{already} photo(s) were already organised — left alone.")
    if videos:
        lines.append(
            f"{videos} video file(s) left untouched — videos are not "
            f"organised (no video metadata reader in the tree).")
    if unreadable:
        lines.append(
            f"{unreadable} image file(s) couldn't be opened — left "
            f"untouched.")
    if over_cap:
        lines.append(
            f"Only the first {_PHOTO_SCAN_CAP} images were moved — "
            f"the folder holds more; run again for the rest.")
    if failed:
        lines.append(
            f"{len(failed)} file(s) could not be moved:")
        for name in failed[:10]:
            lines.append(f"  {name}")
        if len(failed) > 10:
            lines.append(f"  ... and {len(failed) - 10} more.")
    return "\n".join(lines)
