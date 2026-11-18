"""Control Plus split (B26): control_plus_files.py -- moved verbatim from core/desktop/control_plus.py; see the facade for the split map."""

from .control_plus_helpers import _input_blocked  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
from .control_plus_helpers import _is_windows  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
from .control_plus_helpers import _note_input_pressed  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
from .control_plus_helpers import _note_input_released  # INTENTIONAL (B26: cross-part bare name; defined in control_plus_helpers)
from pathlib import Path
import time
import urllib.parse
import urllib.request
import zipfile

def zip_control(action: str, path: str, destination: str = "",
                cancel_token=None) -> str:
    """Zip actions: zip (file/folder -> .zip), unzip (traversal-safe), list_zip.

    B20: ``cancel_token`` is checked per file (zip) and per
    member (unzip) — a Stop pressed mid-archive ends it
    promptly and returns the cancelled DICT (a string return
    would wrap as success). A cancelled zip leaves a partial
    archive behind and NAMES it (it is never deleted — the
    destination may have been a pre-existing file, and
    deleting it would destroy the user's only copy); a
    cancelled unzip leaves the already-extracted members,
    which are complete files, and names how many ran. A
    single huge file has no yield point inside its one
    zf.write call — that case is named here, not hidden.
    """
    action = (action or "").strip().lower()
    valid = ("zip", "unzip", "list_zip")
    if action not in valid:
        return f"Unknown zip action: '{action}'. Use one of: {', '.join(valid)}."
    if not path or not str(path).strip():
        return "Zip control needs a path."
    src = Path(str(path)).expanduser()
    try:
        if action == "zip":
            if not src.exists():
                return f"Path not found: {src}"
            dest = (Path(str(destination)).expanduser() if destination
                    else src.with_suffix(".zip") if src.is_file()
                    else Path(str(src) + ".zip"))
            # Self-destruction guards (v4.4, audit): ZipFile(dest, "w")
            # truncates the destination BEFORE anything is read from
            # the source — a destination equal to the source erases
            # the only copy while reporting success, and a destination
            # inside a zipped folder makes the archive include a
            # partial snapshot of itself.
            src_resolved = src.resolve()
            dest_resolved = dest.resolve()
            if dest_resolved == src_resolved:
                return (
                    f"Refused: the destination is the source itself ({src}) — "
                    "zipping it onto itself would destroy the original. "
                    "Choose a different destination."
                )
            if src.is_dir() and src_resolved in dest_resolved.parents:
                return (
                    f"Refused: the destination {dest} is inside the folder "
                    "being zipped, so the archive would include itself. "
                    "Choose a destination outside the folder."
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
                if src.is_file():
                    zf.write(src, arcname=src.name)
                    count = 1
                else:
                    for item in sorted(src.rglob("*")):
                        # B20: the per-file yield point — a Stop
                        # pressed mid-archive ends the loop here;
                        # the partial archive stays (see the
                        # docstring: it is never deleted) and is
                        # named in the message.
                        if cancel_token is not None and cancel_token.cancelled:
                            from ..funnel.tool_result import cancelled_result
                            return cancelled_result(
                                f"Zipping {src} was cancelled at the user's "
                                f"request after {count} file(s) — the archive "
                                f"at {dest} is incomplete; re-run to rebuild "
                                "it.")
                        if item.is_file():
                            zf.write(item, arcname=item.relative_to(src.parent))
                            count += 1
            return f"Created {dest} ({count} file(s), {dest.stat().st_size} bytes)."
        # unzip / list_zip need an existing archive
        if not src.exists() or not src.is_file():
            return f"Archive not found: {src}"
        if not zipfile.is_zipfile(src):
            return f"{src} is not a valid zip file."
        with zipfile.ZipFile(src) as zf:
            infos = zf.infolist()
            if action == "list_zip":
                if not infos:
                    return f"{src} is an empty zip."
                total_bytes = sum(i.file_size for i in infos)
                lines = [f"• {i.filename} ({i.file_size} bytes)" for i in infos[:50]]
                suffix = f"\n... and {len(infos) - 50} more" if len(infos) > 50 else ""
                return (f"Contents of {src} ({len(infos)} item(s), "
                        f"{total_bytes} bytes uncompressed in total):\n"
                        + "\n".join(lines) + suffix)
            # unzip with path-traversal protection
            dest = (Path(str(destination)).expanduser() if destination
                    else src.parent / src.stem)
            # Zip-bomb caps (v5.47, programme item 18): the shared
            # pre-extraction check from core/voice/local_assets (this
            # module already lazily imports local_assets elsewhere;
            # local_assets -> config, so there is no cycle), run
            # BEFORE the destination is even created and before any
            # member is written — a bomb's header alone refuses it.
            from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/ (D2b: level .., file now in core/desktop/)

            refusal = local_assets.zip_bomb_refusal(
                infos, src.stat().st_size)
            if refusal is not None:
                return refusal + f" Archive: {src}."
            dest.mkdir(parents=True, exist_ok=True)
            dest_resolved = dest.resolve()
            for info in infos:
                target = (dest / info.filename).resolve()
                if target != dest_resolved and dest_resolved not in target.parents:
                    return (
                        f"Refused: archive member '{info.filename}' would extract "
                        f"outside {dest}. Nothing was extracted."
                    )
            # Overwrite honesty (v4.4, audit): extractall replaces
            # same-named files — never silently. Name them.
            overwritten = [
                info.filename for info in infos
                if not info.is_dir() and (dest / info.filename).exists()
            ]
            # B20: extract per member instead of one extractall
            # call, so a Stop pressed mid-extraction ends it at
            # the next member (the traversal check above already
            # validated every target; each extract does the same
            # work extractall would have done for that member).
            # Extracted members are complete files and stay;
            # the rest never run.
            extracted = 0
            for info in infos:
                if cancel_token is not None and cancel_token.cancelled:
                    from ..funnel.tool_result import cancelled_result
                    return cancelled_result(
                        f"Extracting {src} was cancelled at the user's "
                        f"request after {extracted} of {len(infos)} "
                        "item(s) — the extracted files are complete; "
                        "the rest did not run. Re-run to extract "
                        "the rest.")
                zf.extract(info, dest)
                extracted += 1
            result = f"Extracted {len(infos)} item(s) from {src} into {dest}."
            if overwritten:
                shown = ", ".join(overwritten[:10])
                more = (f" (+{len(overwritten) - 10} more)"
                        if len(overwritten) > 10 else "")
                result += (
                    f" Note: {len(overwritten)} existing file(s) were "
                    f"overwritten: {shown}{more}."
                )
            return result
    except Exception as e:  # noqa: BLE001
        return f"Zip operation '{action}' failed: {e}"


# ---------------------------------------------------------------------------
# EYES++ — webcam snapshot
# ---------------------------------------------------------------------------

def webcam_snapshot(save_path: str = "") -> str:
    """Grab one webcam frame and save it as a JPEG. Default: ~/Pictures/nova_webcam_<time>.jpg."""
    # v5.48 (programme item 21): the capture guard wraps the live
    # capture — the sensitive-window denylist refuses FIRST, then
    # the per-session camera opt-in (capture_policy) is checked,
    # and the capture indicator brackets the actual grab. The
    # legacy contract this changes: webcam_snapshot used to run
    # on a model call alone, with no human ask and no regard for
    # a password manager / banking window in front.
    try:
        from .. import capture_guard  # Phase 3 D2b: capture_guard stays flat in core/

        _allowed, _why = capture_guard.ensure_capture_allowed("camera")
    except Exception as e:  # noqa: BLE001 — a broken guard refuses
        return f"Webcam snapshot refused: the capture guard could not run ({e})."
    if not _allowed:
        return f"Webcam snapshot refused: {_why}"
    try:
        from .vision import vision_manager  # Phase 3 D4c: vision now in core/desktop/; D2b interim closed
    except Exception as e:  # noqa: BLE001
        return f"Webcam unavailable: the vision module could not be loaded ({e})."
    capture_guard.note_capture_started("camera")
    try:
        result = vision_manager.capture_webcam()
    except Exception as e:  # noqa: BLE001
        return f"Webcam capture failed: {e}"
    finally:
        capture_guard.note_capture_ended("camera")
    if not result:
        return ("Webcam unavailable: no frame could be captured "
                "(the camera part didn't make it into this build of VYUK — "
                "or no camera is connected / accessible).")
    data = result[0] if isinstance(result, tuple) else result
    if not data:
        return "Webcam capture returned no image data."
    if save_path:
        dest = Path(str(save_path)).expanduser()
    else:
        import datetime

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = Path.home() / "Pictures" / f"nova_webcam_{stamp}.jpg"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return f"Webcam snapshot saved to {dest} ({len(data)} bytes)."
    except Exception as e:  # noqa: BLE001
        return f"Webcam frame was captured ({len(data)} bytes) but could not be saved: {e}"


# ---------------------------------------------------------------------------
# EYES++ — gesture watch (B-R1-8)
# ---------------------------------------------------------------------------

def watch_gestures(duration_s: float = 10.0) -> str:
    """Watch the camera for hand gestures and face presence for a
    bounded time (default 10 s, hard cap 30 s), then report what was
    actually seen. The camera runs only for this watch, only after
    the capture guard allows it for the session, and only through
    the VisionManager hub (core/desktop/vision_gestures.py owns the
    channel). A refused camera, missing weights, or no camera is an
    honest refusal — never a silent fallback, and never a gesture
    the channel did not detect."""
    try:
        duration = float(duration_s or 10.0)
    except (TypeError, ValueError):
        duration = 10.0
    duration = max(1.0, min(duration, 30.0))
    try:
        from .vision import vision_manager
    except Exception as e:  # noqa: BLE001
        return f"Gesture watch unavailable: the vision module could not be loaded ({e})."
    try:
        from . import vision_gestures as _vg
    except Exception as e:  # noqa: BLE001
        return f"Gesture watch unavailable: the gesture channel could not be loaded ({e})."
    try:
        session = vision_manager.start_gesture_session(duration_s=duration)
    except _vg.VisionGesturesUnavailable as e:
        return f"Gesture watch refused: {e}"
    except Exception as e:  # noqa: BLE001 — a broken start refuses
        return f"Gesture watch refused: the gesture session could not start ({e})."
    try:
        import time as _time

        while True:
            reading = vision_manager.poll_gesture_session()
            if reading.get("done"):
                break
            _time.sleep(_vg.POLL_INTERVAL_S)
    finally:
        summary = vision_manager.stop_gesture_session()
    frames = summary.get("frames", 0)
    if frames == 0:
        return ("Gesture watch saw nothing: no camera frame could be "
                "captured (no camera is connected / accessible, or the "
                "camera would not open). Nothing was recorded.")
    gestures = summary.get("gestures_seen") or []
    gesture_text = (", ".join(gestures) if gestures
                    else "no gesture held long enough to report")
    return (
        f"Gesture watch ({summary.get('elapsed_s', duration)} s, "
        f"{frames} frames): gestures seen: {gesture_text}; face present "
        f"in {summary.get('face_frames', 0)} of {frames} frames; hand "
        f"present in {summary.get('hand_frames', 0)} of {frames} frames."
    )


# ---------------------------------------------------------------------------
# HANDS++ — mouse drag
# ---------------------------------------------------------------------------

def drag_mouse(x1: int, y1: int, x2: int, y2: int) -> str:
    """Drag the mouse from (x1, y1) to (x2, y2) with the left button held.

    Wall discipline (v5.9, audit H3): the v4.3 failsafe is
    consulted before anything moves, and both endpoints pass
    through the cursor bounds fence (core/desktop/cursor.py's
    _fence_point — the same fence mouse_control uses), with any
    clamping disclosed in the receipt. The receipt also carries
    the v5.8 identification of what was grabbed.
    """
    try:
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    except (TypeError, ValueError):
        return "Drag needs numeric start and end coordinates (x1, y1, x2, y2)."
    blocked = _input_blocked()
    if blocked:
        return blocked
    from .cursor import _fence_point  # Phase 3 D4d: cursor hub now in core/desktop/; D2b interim closed

    fx1, fy1 = _fence_point(x1, y1)
    fx2, fy2 = _fence_point(x2, y2)
    clamp_notes = []
    if (fx1, fy1) != (x1, y1):
        clamp_notes.append(f"start ({x1}, {y1}) clamped to ({fx1}, {fy1})")
    if (fx2, fy2) != (x2, y2):
        clamp_notes.append(f"end ({x2}, {y2}) clamped to ({fx2}, {fy2})")
    clamp_note = (" (" + "; ".join(clamp_notes)
                  + " — targets are fenced to the screen)") if clamp_notes else ""
    # The mouse is driven with the native Windows SendInput API
    # through the cursor hub — the same handle the cursor tools
    # use. Off Windows there is no drag engine in this build.
    try:
        from .cursor_input import _mouse_controller

        mouse, Button, err = _mouse_controller()
        if mouse is None:
            raise RuntimeError(err)
    except Exception as e:  # noqa: BLE001
        return f"Mouse drag unavailable: {e}"
    # v5.8 receipt: identify what is about to be grabbed, before
    # the press (also captures the foreground title for the
    # post-drag focus note). Best-effort; never blocks the drag.
    id_note = ""
    fg_before = None
    try:
        from .cursor import _identify_click_point  # Phase 3 D4d: cursor hub now in core/desktop/; D2b interim closed

        id_note, fg_before = _identify_click_point(fx1, fy1)
    except Exception:  # noqa: BLE001 — receipt is best-effort
        id_note, fg_before = "", None
    try:
        mouse.position = (fx1, fy1)
        mouse.press(Button.left)
        # v5.48 (programme item 26): the held button is registered
        # for the whole drag; unregistered only when the release
        # below actually lands (a failed release stays tracked
        # for a failsafe trip to rescue).
        _note_input_pressed("button", Button.left)
        try:
            steps = 20
            for i in range(1, steps + 1):
                mouse.position = (
                    int(fx1 + (fx2 - fx1) * i / steps),
                    int(fy1 + (fy2 - fy1) * i / steps),
                )
                time.sleep(0.01)
        finally:
            try:
                mouse.release(Button.left)
            except Exception:
                raise  # stays tracked — see the note above
            _note_input_released("button", Button.left)
    except Exception as e:  # noqa: BLE001
        return f"Could not drag the mouse (no display / input access on this system?): {e}"
    focus_note = ""
    try:
        from .cursor import _focus_change_note  # Phase 3 D4d: cursor hub now in core/desktop/; D2b interim closed

        focus_note = _focus_change_note(fg_before)
    except Exception:  # noqa: BLE001 — receipt is best-effort
        focus_note = ""
    # R1b: the drag was dispatched; the drop landing was not
    # confirmed — mark it honestly unverified.
    from .. import tool_outcome as _outcome  # R1b: seam wiring

    return _outcome.unverified(
        f"Dragged from ({fx1}, {fy1}) to ({fx2}, {fy2})."
        f"{clamp_note}" + id_note + focus_note +
        " (Drag dispatched; the drop landing was not confirmed.)",
        "no drop-landing observation exists for a raw mouse drag")


# ---------------------------------------------------------------------------
# SYSTEM — recycle bin
# ---------------------------------------------------------------------------

def empty_recycle_bin(confirm: bool = False) -> str:
    """Empty the Windows Recycle Bin. Requires confirm=true."""
    if not confirm:
        return ("Blocked by VYUK's trust layer: emptying the Recycle Bin is "
                "permanent and needs confirm=true. Ask again with confirmation.")
    if not _is_windows():
        return "The Recycle Bin is a Windows feature — this system is not Windows."
    try:
        import ctypes

        SHERB_NOCONFIRMATION = 0x00000001
        SHERB_NOPROGRESSUI = 0x00000002
        SHERB_NOSOUND = 0x00000004
        hr = ctypes.windll.shell32.SHEmptyRecycleBinW(
            None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)
        if hr != 0:
            return f"Windows could not empty the Recycle Bin (HRESULT {hr:#x}). It may already be empty."
        return "Recycle Bin emptied."
    except Exception as e:  # noqa: BLE001
        return f"Could not empty the Recycle Bin: {e}"


# ---------------------------------------------------------------------------
# NETWORK — file download
# ---------------------------------------------------------------------------

_MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024  # 25 MB


def download_file(url: str, destination: str = "",
                  cancel_token=None) -> str:
    """Download an http(s) file (max 25 MB) to ~/Downloads or the given path.

    B20: ``cancel_token`` is checked per 64 KB chunk — a Stop
    pressed mid-download ends it promptly, deletes the .part
    (like the over-cap path), and returns the cancelled DICT
    (a string return would wrap as success). The destination
    is never created from a partial download.
    """
    if not url or not str(url).strip():
        return "No URL given."
    url = str(url).strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Refused: only http(s) URLs can be downloaded (got scheme '{parsed.scheme or 'none'}')."
    if destination:
        dest = Path(str(destination)).expanduser()
        if dest.is_dir():
            dest = dest / (Path(parsed.path).name or "nova_download")
    else:
        name = Path(parsed.path).name or "nova_download"
        dest = Path.home() / "Downloads" / name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "NovaAI/1.0"})
        written = 0
        with urllib.request.urlopen(req, timeout=30) as resp:
            length = resp.headers.get("Content-Length")
            if length and int(length) > _MAX_DOWNLOAD_BYTES:
                return f"Refused: the file is {int(length)} bytes, over VYUK's 25 MB download cap."
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as fh:
                while True:
                    # B20: the per-chunk yield point — a Stop
                    # pressed mid-download ends the loop here;
                    # the .part is deleted below, exactly like
                    # the over-cap path (a partial download is
                    # never promoted to the destination).
                    if cancel_token is not None and cancel_token.cancelled:
                        fh.close()
                        tmp.unlink(missing_ok=True)
                        from ..funnel.tool_result import cancelled_result
                        return cancelled_result(
                            f"Download of {url} cancelled at the user's "
                            f"request after {written} bytes — the partial "
                            "download was deleted; nothing was saved.")
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        fh.close()
                        tmp.unlink(missing_ok=True)
                        return "Refused: the download exceeded VYUK's 25 MB cap and was deleted."
                    fh.write(chunk)
            # Integrity (v4.4, audit): a clean EOF is not proof of a
            # complete transfer — when the server declared a length,
            # the received bytes must match it. A truncated file is
            # an error and is NOT promoted to the destination (the
            # .part is kept and named, so nothing is silently lost).
            expected = None
            if length:
                try:
                    expected = int(length)
                except (TypeError, ValueError):
                    expected = None
            if expected is not None and written != expected:
                return (
                    f"Download failed: the connection closed early — got "
                    f"{written} of {expected} bytes from {url}. The "
                    f"incomplete file was kept as {tmp} (not saved as "
                    f"{dest}); try again to resume from scratch."
                )
            tmp.replace(dest)
        return f"Downloaded {url} -> {dest} ({written} bytes)."
    except Exception as e:  # noqa: BLE001
        return f"Download failed: {e}"
