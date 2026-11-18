"""control_system concern extracted from core/control.py (refactor Phase 1 step 4). Behaviour identical; every name here stays importable from core/control."""

import re
import shutil
import subprocess
import sys
from pathlib import Path
from .control_base import (_DATA_DIR, _is_windows, _routed_backend, _truncate, _input_blocked)


def _termination_outcome(psutil_mod, proc_labels):
    """Verify terminated processes actually exited (H16).

    proc_labels is a list of (proc, label). Returns (exited_labels,
    still_running_labels), or None when no verification seam exists
    (psutil without wait_procs AND processes without is_running) —
    callers then keep the legacy "Sent terminate" wording rather
    than claiming an exit nobody checked. Never raises."""
    procs = [p for p, _label in proc_labels]
    if not procs:
        return None
    wait_procs = getattr(psutil_mod, "wait_procs", None)
    if callable(wait_procs):
        try:
            gone, _alive = wait_procs(procs, timeout=2)
            gone_ids = {id(p) for p in gone}
            exited = [lbl for p, lbl in proc_labels if id(p) in gone_ids]
            still = [lbl for p, lbl in proc_labels if id(p) not in gone_ids]
            return exited, still
        except Exception:  # noqa: BLE001 — unverifiable via wait_procs
            return None
    try:
        if not all(hasattr(p, "is_running") for p in procs):
            return None
        import time

        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if not any(p.is_running() for p in procs):
                break
            time.sleep(0.1)
        exited = [lbl for p, lbl in proc_labels if not p.is_running()]
        still = [lbl for p, lbl in proc_labels if p.is_running()]
        return exited, still
    except Exception:  # noqa: BLE001
        return None


def close_application(name: str, confirm: bool = False) -> str:
    """Gracefully terminate processes whose name matches. Requires confirm=true."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.close_application(name, confirm=confirm)
    if not name or not str(name).strip():
        return "No application name given."
    if not confirm:
        return (
            f"Blocked by VYUK's trust layer: closing '{name}' needs confirm=true. "
            "Ask again with confirmation and VYUK will terminate it gracefully."
        )
    try:
        import psutil
    except ImportError:
        return "Closing applications isn't available in this build of VYUK — the process part didn't make it into the package."
    needle = str(name).strip().lower()
    terminated = []
    terminated_procs = []  # (proc, label) — for the H16 exit check
    failures = []
    scan_errors = []
    matched = 0
    try:
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pname = (proc.info.get("name") or "")
                if needle in pname.lower() or pname.lower().replace(".exe", "") == needle:
                    matched += 1
                    label = f"{pname} (pid {proc.info.get('pid')})"
                    try:
                        proc.terminate()
                    except Exception as e:  # noqa: BLE001 — AccessDenied / NoSuchProcess
                        failures.append(f"{label}: {e}")
                    else:
                        terminated.append(label)
                        terminated_procs.append((proc, label))
            except Exception as e:  # noqa: BLE001 — process vanished or is inaccessible
                scan_errors.append(str(e))
    except Exception as e:  # noqa: BLE001
        return f"Could not scan processes: {e}"
    if not terminated:
        if matched:
            detail = "; ".join(failures) if failures else "termination failed without a reported error"
            privilege_note = (
                " The app may be running with higher privileges than VYUK"
                if any("denied" in failure.lower() or "access" in failure.lower()
                       for failure in failures)
                else ""
            )
            return (f"{matched} running process(es) matched '{name}', but none could be closed: "
                    f"{detail}.{privilege_note}")
        if scan_errors:
            return (f"No inspectable running process matched '{name}', but "
                    f"{len(scan_errors)} process(es) could not be inspected while scanning, "
                    "so that result may be incomplete.")
        return f"No running process matched '{name}'."
    result = f"Sent terminate to {len(terminated)} process(es): " + ", ".join(terminated)
    # H16: terminate is a request — verify the exits before the
    # receipt is allowed to read as "closed".
    outcome = _termination_outcome(psutil, terminated_procs)
    if outcome is not None:
        exited, still = outcome
        if still:
            result += (" Still running after the terminate request: "
                       + ", ".join(still) + " — they did not exit.")
            if exited:
                result += f" Confirmed exited: {', '.join(exited)}."
        else:
            result += (f" Confirmed closed: all {len(terminated_procs)} "
                       "process(es) exited.")
    if failures:
        result += (f". {len(failures)} matched process(es) could not be closed: "
                   + "; ".join(failures))
    if scan_errors:
        result += (f". {len(scan_errors)} other process(es) could not be inspected "
                   "while scanning.")
    return result


_MEDIA_VK = {
    "play_pause": 0xB3, "play": 0xB3, "pause": 0xB3,
    "next": 0xB0, "previous": 0xB1, "prev": 0xB1, "stop": 0xB2,
}


def media_control(action: str) -> str:
    """Media keys: play_pause/next/previous/stop."""
    action = (action or "").strip().lower()
    if action not in _MEDIA_VK:
        return f"Unknown media action: '{action}'. Use one of: play_pause, next, previous, stop."
    # H12: a media key is still a dispatched hand — it stops when
    # the failsafe trips, like the other hands in this module.
    blocked = _input_blocked()
    if blocked:
        return blocked
    if not _is_windows():
        return "Media keys via virtual-key codes are only available on Windows — this system is not Windows."
    try:
        # GAPS-17: was two deprecated keybd_event calls — the last
        # input dispatch in the tree outside the shared hub. A
        # media key is a plain VK down/up, exactly what the hub's
        # _sendinput_key sends; same wire events, same failsafe
        # check above, same unverified outcome below.
        from .cursor_input import _sendinput_key

        vk = _MEDIA_VK[action]
        _sendinput_key(vk)
        _sendinput_key(vk, key_up=True)
        # R1b: the key was sent; no OS read-back exists for whether
        # any app acted on it — mark it honestly unverified.
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        return _outcome.unverified(
            "Media action sent: %s (sent — whether any app acted on "
            "it was not confirmed.)" % action,
            "no OS read-back exists for a sent media key")
    except Exception as e:  # noqa: BLE001
        return f"Media control failed: {e}"


def set_brightness(level: int) -> str:
    """Set screen brightness 0-100 via Windows WMI (PowerShell). Guarded elsewhere.

    H7: WmiSetBrightness reports failure through its RETURN VALUE,
    not the process exit code, and it is a known silent no-op on
    many external/DDC monitors — so the method's result is emitted
    and parsed, and the level in effect is read back via
    WmiMonitorBrightness before anything is claimed."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        return "Brightness level must be a number between 0 and 100."
    requested = level
    level = max(0, min(100, level))
    clamp_note = ""
    if level != requested:
        clamp_note = f" (asked for {requested} — clamped to {level})"
    if not _is_windows():
        return "Brightness control requires Windows (WMI monitor methods) — this system is not Windows, so brightness was not changed."
    try:
        ps_cmd = (
            "$m = (Get-WmiObject -Namespace root/WMI -Class "
            "WmiMonitorBrightnessMethods); "
            f"$r = $m.WmiSetBrightness(1,{level}); "
            '"RESULT=$r"; '
            "$b = (Get-WmiObject -Namespace root/WMI -Class "
            "WmiMonitorBrightness | Select-Object -First 1); "
            'if ($b) { "CURRENT=$($b.CurrentBrightness)" }'
        )
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=15,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            return f"Could not set brightness via WMI: {_truncate(detail, 300)}"
        out = proc.stdout or ""
        # The set call may return one value per monitor; any
        # non-zero value is a failure report from WMI.
        set_result = None
        mres = re.search(r"RESULT=([0-9][0-9 ]*)", out)
        if mres:
            values = [int(v) for v in mres.group(1).split()]
            if values:
                set_result = next((v for v in values if v != 0), 0)
        current = None
        mcur = re.search(r"CURRENT=([0-9]+)", out)
        if mcur:
            current = int(mcur.group(1))
        if set_result not in (None, 0):
            msg = ("Could not set brightness: WmiSetBrightness reported "
                   f"failure (returned {set_result}).")
            if current is not None:
                msg += f" The brightness currently in effect is {current}%."
            return msg + clamp_note
        if current is not None:
            if current == level:
                return (f"Brightness set to {level}% — read back from the "
                        f"monitor and confirmed at {current}%.{clamp_note}")
            return (
                f"Brightness was asked to be {level}% and WMI reported "
                f"success, but the monitor reads back {current}% — it may "
                "not have applied the change (many external monitors "
                f"ignore WMI brightness).{clamp_note}"
            )
        return (f"Brightness set to {level}% (WMI reported success; the "
                f"level could not be read back to confirm).{clamp_note}")
    except Exception as e:  # noqa: BLE001
        return f"Could not set brightness: {e}"


def power_control(action: str, confirm: bool = False) -> str:
    """Power actions: shutdown/restart (require confirm=true) and sleep."""
    action = (action or "").strip().lower()
    if action not in ("shutdown", "restart", "sleep"):
        return f"Unknown power action: '{action}'. Use one of: shutdown, restart, sleep."
    if action in ("shutdown", "restart") and not confirm:
        return (
            f"Blocked by VYUK's trust layer: '{action}' needs confirm=true. "
            "Ask again with confirmation if you really mean it."
        )
    if action == "sleep":
        if not _is_windows():
            return "Sleep via SetSuspendState is only available on Windows — this system is not Windows."
        try:
            import ctypes

            # ForceCritical=False (v4.4): the old call forced the
            # suspend without letting applications save state, and
            # ignored the result. Check the BOOL: a refused suspend
            # must be reported as refused, never as "going to sleep".
            ok = ctypes.windll.PowrProf.SetSuspendState(False, False, False)
            if not ok:
                return "Could not put the system to sleep: the system refused the suspend request."
            return "System is going to sleep."
        except Exception as e:  # noqa: BLE001
            return f"Could not put the system to sleep: {e}"
    # shutdown / restart (confirm already checked)
    try:
        if _is_windows():
            flag = "/s" if action == "shutdown" else "/r"
            proc = subprocess.run(["shutdown", flag, "/t", "5"], capture_output=True, text=True, timeout=10)
        else:
            flag = "-h" if action == "shutdown" else "-r"
            proc = subprocess.run(["shutdown", flag, "+1"], capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            # v4.4: a failed schedule (e.g. no privilege) was reported
            # as "scheduled" — assert only what the OS confirmed.
            detail = (proc.stderr or proc.stdout or "").strip()
            suffix = f": {detail}" if detail else ""
            return f"Could not {action}: the system command failed (exit code {proc.returncode}){suffix}"
        return f"{action.capitalize()} scheduled (short delay so it can be cancelled with 'shutdown /a' on Windows)."
    except Exception as e:  # noqa: BLE001
        return f"Could not {action}: {e}"


def write_clipboard(text: str) -> str:
    """Copy text to the system clipboard, verified by paste-back (H9).

    pyperclip.copy() returning quietly is not proof the clipboard
    holds the text (another app can hold the clipboard open or
    overwrite it), so the clipboard is read straight back and a
    mismatch is reported as the failure it is. The read is cheap and
    guarded: when paste-back is unavailable, the legacy success
    wording is kept rather than inventing a failure."""
    if text is None:
        return "Nothing to copy: no text was given."
    text = str(text)
    try:
        import pyperclip

        pyperclip.copy(text)
    except ImportError:
        return "Clipboard writing isn't available in this build of VYUK — the clipboard part didn't make it into the package."
    except Exception as e:  # noqa: BLE001
        return f"Could not write to the clipboard: {e}"
    try:
        current = pyperclip.paste()
    except Exception:  # noqa: BLE001 — cannot verify; report as before
        current = text
    if isinstance(current, str) and current != text:
        return (
            "The copy did not stick: after writing, the clipboard holds "
            f"different content ({len(current)} character(s) instead of "
            f"{len(text)}) — another app may have changed it, so the "
            "text was NOT copied."
        )
    collapsed = " ".join(text.split())
    preview = collapsed[:60]
    if preview:
        tail = "…" if len(collapsed) > 60 else ""
        suffix = f": '{preview}{tail}'"
    else:
        suffix = ""
    return f"Copied {len(text)} character(s) to the clipboard{suffix}."


def open_path(path: str) -> str:
    """Open a file or folder with the OS default application."""
    if not path or not str(path).strip():
        return "No path given."
    target = Path(str(path)).expanduser()
    if not target.exists():
        return f"Path not found: {target}"
    try:
        # R1b: the launch was requested; no read-back exists for
        # whether the app opened it — mark it honestly unverified.
        from .. import tool_outcome as _outcome  # R1b: seam wiring

        if _is_windows():
            import os

            os.startfile(str(target))  # noqa: S606 — Windows-only API
            return _outcome.unverified(
                "Opened %s. (Launch requested — whether the app "
                "opened it was not confirmed.)" % target,
                "no read-back exists for a launched file")
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener) is None:
            return f"Could not open {target}: no '{opener}' available on this system."
        subprocess.Popen([opener, str(target)])
        return _outcome.unverified(
            "Opened %s. (Launch requested — whether the app opened "
            "it was not confirmed.)" % target,
            "no read-back exists for a launched file")
    except Exception as e:  # noqa: BLE001
        return f"Could not open {target}: {e}"


def _capture_screen_bytes(include_cursor: bool = False):
    """Return (jpeg_bytes, mime) via vision_manager, or raise."""
    from .vision import vision_manager  # Phase 3 D4c: vision now in core/desktop/; D2b interim closed

    if include_cursor:
        return vision_manager.capture_screen(include_cursor=True)
    result = vision_manager.capture_screen()
    if isinstance(result, tuple):
        return result[0], result[1] if len(result) > 1 else "image/jpeg"
    return result, "image/jpeg"


def take_screenshot(save_path: str = "", delay_seconds: float = 0,
                    include_cursor: bool = False) -> str:
    """Capture the screen. Saves to save_path if given, else notes
    it was captured for viewing. delay_seconds waits first (menus
    and hover states survive); include_cursor marks the cursor's
    position with an arrow in the image."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.take_screenshot(
            save_path=save_path, delay_seconds=delay_seconds,
            include_cursor=include_cursor)
    try:
        delay = max(0.0, min(float(delay_seconds or 0), 10.0))
    except (TypeError, ValueError):
        delay = 0.0
    waited = ""
    if delay > 0:
        import time as _time

        _time.sleep(delay)
        waited = f" after waiting {delay:g} second(s)"
    cursor_note = ""
    try:
        data, mime = _capture_screen_bytes(include_cursor=bool(include_cursor))
        if include_cursor:
            cursor_note = (" (the cursor's position is marked with "
                           "an arrow)")
    except Exception as e:  # noqa: BLE001
        return (
            f"Could not capture a screenshot: {e}. "
            "Screen capture needs a display — it is unavailable on a headless system."
        )
    if not data:
        return "Screenshot capture returned no data."
    if save_path:
        try:
            dest = Path(str(save_path)).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return (f"Screenshot saved to {dest} ({len(data)} bytes, {mime})"
                    f"{waited}{cursor_note}.")
        except Exception as e:  # noqa: BLE001
            return f"Screenshot was captured ({len(data)} bytes) but could not be saved: {e}"
    return (f"Screenshot captured for the assistant to view "
            f"({len(data)} bytes, {mime}){waited}{cursor_note}.")


def get_active_window() -> str:
    """Title of the foreground window. Windows only, guarded elsewhere."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.get_active_window()
    if not _is_windows():
        return "Active-window detection is only available on Windows — this system is not Windows."
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "No foreground window found."
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value or "(untitled window)"
        return f"Active window: {title}"
    except Exception as e:  # noqa: BLE001
        return f"Could not read the active window: {e}"


OCR_UPSCALE = 2


_OCR_BINARISE_THRESHOLD = 160


def _capture_native_image(full_screen: bool = False):
    """(PIL Image, meta) at native resolution via the vision
    manager — the patchable seam for every OCR capture (tests fake
    vision.capture_native_image, never a display)."""
    from .vision import vision_manager  # Phase 3 D4c: vision now in core/desktop/; D2b interim closed

    return vision_manager.capture_native_image(full_screen=full_screen)


def _prepare_ocr_image(image):
    """(prepared_image, scale): greyscale, x2 upscale, light
    binarisation (v5.44, E2b). The returned scale is the EXACT
    factor the prepared image is larger than the native frame by
    (prepared.width / native.width), so word boxes divide back to
    native pixels with no ratio quantisation."""
    from PIL import Image

    src = image.convert("L")
    width, height = src.size
    upscaled = src.resize(
        (max(1, width * OCR_UPSCALE), max(1, height * OCR_UPSCALE)),
        Image.Resampling.LANCZOS)
    prepared = upscaled.point(
        lambda p: 255 if p >= _OCR_BINARISE_THRESHOLD else 0)
    scale = (prepared.width / width) if width else float(OCR_UPSCALE)
    return prepared, scale


def _ocr_lang_config():
    """(config, lang, note) for one OCR call, from local_assets'
    resolver (v5.44, E2c/d)."""
    from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/ (D2b: level .., file now in core/desktop/)

    return local_assets.ocr_tesseract_config()


def _ocr_image_string(image, pytesseract):
    """(text, note): image_to_string over the prepared image with
    the resolved language + explicit config."""
    config, lang, note = _ocr_lang_config()
    prepared, _scale = _prepare_ocr_image(image)
    text = pytesseract.image_to_string(prepared, lang=lang,
                                       config=config)
    return (text or "").strip(), note


def _ocr_image_words(image, meta, pytesseract, Output):
    """(words, note): image_to_data over the prepared image; each
    word is {text, left, top, right, bottom, width, height} in
    TRUE screen pixels — the OCR box divided by the exact upscale
    factor and offset by the capture's crop origin, so a box OCR
    found inside the foreground window names the screen point it
    is actually at."""
    config, lang, note = _ocr_lang_config()
    prepared, scale = _prepare_ocr_image(image)
    data = pytesseract.image_to_data(
        prepared, lang=lang, config=config, output_type=Output.DICT)
    origin_x = int((meta or {}).get("crop_left") or 0)
    origin_y = int((meta or {}).get("crop_top") or 0)
    words = []
    for i, raw in enumerate(data.get("text", [])):
        word = (raw or "").strip()
        if not word:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError, KeyError, IndexError):
            conf = -1
        if conf < 0:
            continue
        try:
            left_f = float(data["left"][i]) / scale
            top_f = float(data["top"][i]) / scale
            right_f = (float(data["left"][i])
                       + float(data["width"][i])) / scale
            bottom_f = (float(data["top"][i])
                        + float(data["height"][i])) / scale
        except (TypeError, ValueError, KeyError, IndexError):
            continue
        left, top = origin_x + round(left_f), origin_y + round(top_f)
        right = origin_x + round(right_f)
        bottom = origin_y + round(bottom_f)
        words.append({
            "text": word.lower(), "left": left, "top": top,
            "right": right, "bottom": bottom,
            "width": right - left, "height": bottom - top,
        })
    return words, note


def _ocr_captures(full_screen: bool = False):
    """Yield (image, meta) capture attempts for one OCR read: the
    requested capture first (the foreground crop by default), then
    — only when that capture WAS a crop — a full-screen capture.
    The caller takes the second attempt only when the first's OCR
    yield is empty (v5.44, E7: the fallback lives where the yield
    is known)."""
    image, meta = _capture_native_image(full_screen=full_screen)
    yield image, meta
    if not full_screen and (meta or {}).get("cropped"):
        image2, meta2 = _capture_native_image(full_screen=True)
        yield image2, meta2


def ocr_words_native(full_screen: bool = False):
    """(words, error, meta): native-resolution OCR word boxes for
    the screen (v5.44, E2e), with the E7 crop -> full-screen
    fallback when a cropped capture yields no words. `error` is
    None whenever OCR ran (words may still be [] — genuinely no
    readable words); otherwise it is the honest line to return."""
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        return None, ("Text reading isn't available in this build of "
                      "VYUK — the OCR part didn't make it into the "
                      "package."), None
    except Exception as e:  # noqa: BLE001
        return None, (f"OCR is unavailable on this PC: pytesseract "
                      f"could not be loaded ({e})."), None
    from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/ (D2b: level .., file now in core/desktop/)

    if not local_assets.configure_pytesseract(pytesseract):
        return None, ("Text reading isn't set up on this machine yet "
                      "— the OCR engine that comes with VYUK isn't in "
                      "place. It arrives with the installer; nothing "
                      "for you to download."), None
    try:
        attempts = _ocr_captures(full_screen=full_screen)
        image, meta = next(attempts)
    except Exception as e:  # noqa: BLE001
        return None, (f"Could not capture the screen to read its "
                      f"text: {e}. Screen capture needs a display — "
                      "it is unavailable on a headless system."), None
    while True:
        try:
            words, _note = _ocr_image_words(image, meta, pytesseract,
                                            Output)
        except Exception as e:  # noqa: BLE001
            return None, f"OCR failed: {e}", meta
        if words or not (meta or {}).get("cropped"):
            return words, None, meta
        try:
            image, meta = next(attempts)
        except StopIteration:
            return words, None, meta
        except Exception as e:  # noqa: BLE001
            return None, (f"Could not capture the screen to read its "
                          f"text: {e}. Screen capture needs a "
                          "display — it is unavailable on a headless "
                          "system."), meta


def ocr_screen() -> str:
    """Capture the screen and extract visible text with Tesseract OCR, if installed."""
    try:
        import pytesseract
    except ImportError:
        return "Text reading isn't available in this build of VYUK — the OCR part didn't make it into the package."
    except Exception as e:  # noqa: BLE001
        return f"Text reading couldn't start: the OCR library failed to load ({e})."
    from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/ (D2b: level .., file now in core/desktop/)

    if not local_assets.configure_pytesseract(pytesseract):
        return "Text reading isn't set up on this machine yet — the OCR engine that comes with VYUK isn't in place. It arrives with the installer; nothing for you to download."
    notes: list[str] = []
    text = ""
    fell_back = False
    try:
        attempts = _ocr_captures()
        image, meta = next(attempts)
    except Exception as e:  # noqa: BLE001
        return f"Could not capture the screen for OCR: {e}"
    while True:
        try:
            text, note = _ocr_image_string(image, pytesseract)
        except Exception as e:  # noqa: BLE001
            return f"OCR failed (is the Tesseract binary installed and on PATH?): {e}"
        if note and note not in notes:
            notes.append(note)
        if text or not (meta or {}).get("cropped"):
            break
        # E7 fallback, at the seam where the yield is known: the
        # foreground crop read as empty, so read the whole screen.
        try:
            image, meta = next(attempts)
        except StopIteration:
            break
        except Exception as e:  # noqa: BLE001
            return f"Could not capture the screen for OCR: {e}"
        fell_back = True
    tail: list[str] = []
    if fell_back and text:
        tail.append("The focused window held no readable text, so "
                    "I read the whole screen instead.")
    tail.extend(notes)
    suffix = (" " + " ".join(tail)) if tail else ""
    if not text:
        return "OCR ran, but no readable text was found on screen." + suffix
    # v5.70 (Life Admin, LA-2): S-6 content denylist on the raw OCR
    # text — an Aadhaar/OTP match REFUSES, never redact-and-continue.
    # Fail-closed: if the check itself cannot run, OCR reports that
    # honestly instead of emitting unchecked screen text.
    try:
        from .. import capture_guard  # Phase 3 D2b: capture_guard stays flat in core/

        refusal = capture_guard.check_text(text)
    except Exception:  # noqa: BLE001 — fail-closed, see below
        refusal = "refusal-check-unavailable"
    if refusal is not None:
        if refusal == "refusal-check-unavailable":
            return ("OCR held back: the sensitive-content safety "
                    "check could not run, so no screen text is "
                    "emitted.")
        return refusal
    wrapped = _truncate(text, 1500)
    try:
        # v5.3 (H2): screen text is untrusted content — envelope
        # + injection screen, like every other external source.
        from .. import injection  # Phase 3 D2b: injection stays flat in core/

        wrapped = injection.wrap_untrusted("screen text", wrapped)
    except Exception:  # noqa: BLE001 — wrapping must never break OCR
        pass
    return "On-screen text:\n" + wrapped + suffix


def get_activity_log(lines: int = 20, contains: str = "") -> str:
    """Return the last N lines of VYUK's tool activity log,
    optionally only the lines containing a word."""
    try:
        lines = int(lines)
    except (TypeError, ValueError):
        lines = 20
    lines = max(1, min(lines, 200))
    log_path = _DATA_DIR / "activity.log"
    if not log_path.exists():
        return "No activity logged yet — data/activity.log does not exist."
    try:
        all_lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:  # noqa: BLE001
        return f"Could not read the activity log: {e}"
    if not all_lines:
        return "Activity log is empty."
    needle = str(contains or "").strip()
    if needle:
        matches = [ln for ln in all_lines
                   if needle.lower() in ln.lower()]
        if not matches:
            return (f"No activity log lines contain '{needle}' "
                    f"(the log has {len(all_lines)} line(s)).")
        tail = matches[-lines:]
        return (f"Last {len(tail)} of {len(matches)} activity log "
                f"line(s) containing '{needle}':\n"
                + "\n".join(tail))
    tail = all_lines[-lines:]
    return f"Last {len(tail)} activity log line(s):\n" + "\n".join(tail)
