"""Capture-scroll implementations for the hands3 skills pack.

Split out of core/skills_window_control.py by SKILLS SPLIT PHASE A BATCH 2
(capture-scroll + facade close-out). Same functions and constants,
verbatim — only the module-level import block is new. The window
helpers this module calls (_foreground_hwnd, _monitor_for_window,
_monitors, _resolve_monitor) live in the window-manip module; that
cross-import sits at the bottom, after all definitions, so this module
imports cleanly in any order — facade-first or standalone.
"""

import time
from pathlib import Path

_NOT_WINDOWS = "this system is not Windows, so nothing was changed."

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_BASELINE_NAME = "screen_diff_baseline.png"

_TYPE_CLIPBOARD_CAP = 2000
_MAX_WAYPOINTS = 12





def _input_blocked():
    """The house failsafe check, via control.py (None = free to act)."""
    try:
        from .desktop.control import _input_blocked as shared_check  # Phase 3 D4a: control hub moved to core/desktop/

        return shared_check()
    except Exception:  # noqa: BLE001 — a broken failsafe must not
        return None  # disable the hands wholesale (house convention)



# ---------------------------------------------------------------------------
# OCR helpers — v5.44 (E2e): the word boxes come from core/desktop/control.py's
# native-resolution pipeline (ocr_words_native): a dedicated full-res
# frame (the foreground window's rectangle by default, E7), x2 upscale
# + binarisation, explicit Tesseract config + preference language, and
# boxes already in true screen pixels. The pre-E2 helper OCR'd the
# 1024-px capture and scaled its boxes back by ratio — that scaling,
# and this module's duplicate of the click_text pattern, are gone.
# ---------------------------------------------------------------------------


def _ocr_words():
    """(words, error): words are dicts with lowercased text and a
    screen-pixel box (left/top/right/bottom); error is None on
    success, else the honest line to return to the user."""
    try:
        from .desktop.control import ocr_words_native  # Phase 3 D4a: control hub moved to core/desktop/

        words, error, _meta = ocr_words_native()
    except Exception as e:  # noqa: BLE001 — the seam must not raise
        return None, f"OCR failed: {e}"
    if error is not None:
        return None, error
    return words, None



def _inspected_scroll_point(words):
    """(x, y) screen pixels to scroll at — the place that was
    actually inspected — or None to keep the pointer position.
    Desktop programme, D-03: scroll_until_found used to scroll at
    wherever the pointer happened to be, while its OCR inspected
    the foreground crop. The inspected location is now, in order:
    the centre of the OCR words' bounding box (the content just
    read), else the centre of the last capture's crop rectangle
    (the foreground crop itself), else None (nothing inspected
    is on record — the pointer fallback, unchanged). Never
    raises; a malformed record falls through to the next source."""
    try:
        boxes = [(int(w["left"]), int(w["top"]), int(w["right"]),
                  int(w["bottom"])) for w in (words or [])
                  if all(k in w for k in ("left", "top", "right", "bottom"))]
    except (TypeError, ValueError):
        boxes = []
    boxes = [b for b in boxes if b[2] > b[0] and b[3] > b[1]]
    if boxes:
        left = min(b[0] for b in boxes)
        top = min(b[1] for b in boxes)
        right = max(b[2] for b in boxes)
        bottom = max(b[3] for b in boxes)
        return (left + right) // 2, (top + bottom) // 2
    try:
        from .desktop import vision as _vision  # Phase 3 D4c: vision hub moved to core/desktop/

        info = _vision.last_capture_info()
    except Exception:  # noqa: BLE001
        info = None
    if info:
        try:
            if info.get("cropped"):
                cl = int(info["crop_left"])
                ct = int(info["crop_top"])
                cw = int(info["crop_width"])
                ch = int(info["crop_height"])
                if cw > 0 and ch > 0:
                    return cl + cw // 2, ct + ch // 2
        except (KeyError, TypeError, ValueError):
            pass
    return None



def _phrase_centres(words, phrase: str):
    """Centres of every place the phrase's words appear consecutively
    (case-insensitive), as (x, y) screen pixels."""
    target = (phrase or "").strip().lower().split()
    if not target or not words:
        return []
    n = len(target)
    centres = []
    for i in range(len(words) - n + 1):
        span = words[i:i + n]
        if [w["text"] for w in span] != target:
            continue
        left = min(w["left"] for w in span)
        top = min(w["top"] for w in span)
        right = max(w["right"] for w in span)
        bottom = max(w["bottom"] for w in span)
        centres.append(((left + right) // 2, (top + bottom) // 2))
    return centres



# ---------------------------------------------------------------------------
# Dragging with the button held — the cursor seam (drag_cursor's
# shape, chained over several segments): press, follow the planned
# eased paths, release in a finally whatever happens.
# ---------------------------------------------------------------------------


def _drag_hold(points, total_duration: float):
    """Drag through points [(x, y), …] with the left button held.

    Returns None on success, else the message to report (the
    cursor's takeover abort, or the failure line)."""
    from .desktop import cursor  # Phase 3 D4d: cursor hub moved to core/desktop/

    if cursor._failsafe_tripped():
        return cursor._FAILSAFE_MSG
    mouse, Button, err = cursor._mouse_controller()
    if mouse is None:
        return err
    pts = [cursor._fence_point(x, y) for x, y in points]
    segments = max(1, len(pts) - 1)
    per_segment = max(0.0, float(total_duration or 0.0)) / segments
    try:
        cursor._set_position(mouse, pts[0][0], pts[0][1])
        mouse.press(Button.left)
        # Same v5.48 pattern as the canonical drag: the held
        # button is registered for the whole drag, so a failsafe
        # trip mid-drag releases exactly this button. (D-06:
        # this call was missing here — the finally-release
        # existed, but the failsafe registry never knew about
        # the hold, so a trip released nothing it held.)
        cursor._note_input_pressed("button", Button.left)
    except Exception as e:  # noqa: BLE001
        return (f"Could not start the drag (no display / input access "
                f"on this system?): {e}")
    abort = None
    try:
        for i in range(segments):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            abort = cursor._follow_path(
                mouse, cursor._plan_path(x1, y1, x2, y2, per_segment))
            if abort:
                break
    finally:
        try:
            mouse.release(Button.left)
        except Exception:  # noqa: BLE001
            pass
        else:
            # Mirror the canonical drag: unregister only when
            # the release itself succeeded — a failed release
            # leaves the hold tracked so a trip can still
            # release it.
            cursor._note_input_released("button", Button.left)
    return abort



# ---------------------------------------------------------------------------
# TYPE CLIPBOARD — real keystrokes for fields that block paste
# ---------------------------------------------------------------------------


def type_clipboard() -> str:
    """Type the clipboard's text into the focused app as keystrokes."""
    blocked = _input_blocked()
    if blocked:
        return blocked
    try:
        import pyperclip
    except ImportError:
        return "Clipboard reading isn't available in this build of VYUK — the clipboard part didn't make it into the package."
    except Exception as e:  # noqa: BLE001
        return f"Could not load the clipboard library: {e}"
    try:
        text = pyperclip.paste()
    except Exception as e:  # noqa: BLE001
        return f"Could not read the clipboard: {e}"
    if not isinstance(text, str) or not text:
        return ("The clipboard is empty, or holds something that isn't "
                "text (an image or files), so there's nothing to type.")
    total = len(text)
    to_type = text[:_TYPE_CLIPBOARD_CAP]
    try:
        from .desktop.control import type_text  # Phase 3 D4a: control hub moved to core/desktop/

        receipt = type_text(to_type)
    except Exception as e:  # noqa: BLE001
        return f"Could not type the clipboard's text: {e}"
    # R1b: type_text may now return an Outcome — read it through
    # str(...) for the prefix check.
    if not str(receipt).startswith("Typed "):
        return receipt  # a failure/refusal line — pass it through untouched
    # B13: the inner type_text already ran the vision landing check
    # on this same text and screen — an Outcome from it carries
    # that verdict, so return it as-is and never check twice. Only
    # a plain-string receipt (the routed-backend path, which never
    # checks) gets a check of its own here.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    if _outcome.is_outcome(receipt):
        return receipt
    if total > _TYPE_CLIPBOARD_CAP:
        return _outcome.hands_landing_check(
            "type", to_type,
            f"Typed the first {_TYPE_CLIPBOARD_CAP} of the "
            f"clipboard's {total} characters into the focused app "
            f"(the cap for typed input) — the remaining "
            f"{total - _TYPE_CLIPBOARD_CAP} were not typed. "
            "(Sent as keystrokes; whether the text landed was not "
            "confirmed.)")
    return _outcome.hands_landing_check(
        "type", to_type,
        f"Typed the clipboard's {total} character(s) into the "
        "focused app as keystrokes. (Sent as keystrokes; whether "
        "the text landed was not confirmed.)")



# ---------------------------------------------------------------------------
# CLICK AND DRAG PATH — a drag that bends through waypoints
# ---------------------------------------------------------------------------


def _parse_points(points: str):
    """(points, error): parse 'x,y;x,y;…' into [(x, y), …]."""
    raw = (points or "").strip()
    if not raw:
        return None, ("No waypoints given. Points look like "
                      "'x,y;x,y;…' — e.g. '100,200;400,200;400,500'.")
    pts = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        bits = chunk.split(",")
        if len(bits) != 2:
            return None, (f"Couldn't read the waypoint '{chunk}' — each "
                          "point must be 'x,y', e.g. '100,200'.")
        try:
            pts.append((int(float(bits[0])), int(float(bits[1]))))
        except ValueError:
            return None, (f"Couldn't read the waypoint '{chunk}' — "
                          "coordinates must be whole numbers.")
    if len(pts) < 2:
        return None, ("A drag needs at least two waypoints — where it "
                      "starts and where it ends.")
    if len(pts) > _MAX_WAYPOINTS:
        return None, (f"That's {len(pts)} waypoints — the cap is "
                      f"{_MAX_WAYPOINTS}. Split the drag into two calls.")
    return pts, ""



def click_and_drag_path(points: str, duration_seconds: float = 2.0) -> str:
    """Drag through a list of waypoints with the left button held."""
    pts, err = _parse_points(points)
    if pts is None:
        return err
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError):
        return "duration_seconds must be a number of seconds (0.2–20)."
    duration = max(0.2, min(duration, 20.0))
    abort = _drag_hold(pts, duration)
    if abort:
        if "aborted" in abort.lower():
            return f"{abort} (Drag released where it stopped.)"
        return abort
    route = " → ".join(f"({x}, {y})" for x, y in pts)
    # R1b: the drag was dispatched along the waypoints; the drop
    # landing was not confirmed — mark it honestly unverified.
    from . import tool_outcome as _outcome  # R1b: seam wiring

    return _outcome.unverified(
        f"Dragged through {len(pts)} waypoints — {route} — and "
        "released. (Drag dispatched; the drop landing was not "
        "confirmed.)",
        "no drop-landing observation exists for a raw cursor drag")



# ---------------------------------------------------------------------------
# SCROLL UNTIL FOUND — OCR after every scroll, honest stopping rules
# ---------------------------------------------------------------------------


def scroll_until_found(text: str, max_attempts: int = 6,
                       scroll_amount: int = 5) -> str:
    """Scroll down step by step until `text` appears on screen (OCR)."""
    phrase = (text or "").strip()
    if not phrase:
        return "Tell me what word or phrase to scroll for."
    blocked = _input_blocked()
    if blocked:
        return blocked
    try:
        attempts = int(max_attempts)
    except (TypeError, ValueError):
        attempts = 6
    attempts = max(1, min(attempts, 10))
    try:
        amount = int(scroll_amount)
    except (TypeError, ValueError):
        amount = 5
    amount = max(1, min(amount, 10))
    try:
        from .desktop.cursor import scroll_cursor  # Phase 3 D4d: cursor hub moved to core/desktop/
    except Exception as e:  # noqa: BLE001
        return f"Scrolling is unavailable on this PC: {e}"
    previous_signature = None
    scrolls_done = 0
    for round_no in range(attempts + 1):
        words, err = _ocr_words()
        if err:
            return err
        centres = _phrase_centres(words, phrase)
        if centres:
            x, y = centres[0]
            more = (f" (first of {len(centres)} matches on screen)"
                    if len(centres) > 1 else "")
            scrolled = (f" after {scrolls_done} scroll(s)"
                        if scrolls_done else " — it was already on screen")
            return (f"Found '{phrase}'{scrolled}, at about ({x}, {y}) "
                    f"on screen{more}.")
        signature = " ".join(w["text"] for w in words)
        if round_no > 0 and signature == previous_signature:
            return (f"I scrolled {scrolls_done} time(s), but the page "
                    f"stopped moving — the screen reads the same as "
                    f"before the last scroll — and '{phrase}' never "
                    "appeared. It may not be on this page, or this "
                    "window doesn't scroll with the wheel.")
        previous_signature = signature
        if round_no == attempts:
            break
        # D-03: scroll where the inspection happened — the OCR
        # content's centre, else the last capture's crop centre —
        # never wherever the pointer happens to be.
        point = _inspected_scroll_point(words)
        if point is not None:
            result = scroll_cursor(-amount, x=point[0], y=point[1])
        else:
            result = scroll_cursor(-amount)  # negative = down, revealing more
        if not result.startswith("Scrolled"):
            return result
        scrolls_done += 1
        time.sleep(0.6)
    return (f"Scrolled {scrolls_done} time(s) looking for '{phrase}' and "
            "read the screen after each one — it never appeared. It may "
            "be worded differently, or OCR may have missed it (small or "
            "low-contrast text is hard to read).")



# ---------------------------------------------------------------------------
# SCREEN DIFF — now vs a saved baseline, described in words
# ---------------------------------------------------------------------------

_DIFF_REGIONS = (("top-left", "top-centre", "top-right"),
                 ("centre-left", "centre", "centre-right"),
                 ("bottom-left", "bottom-centre", "bottom-right"))

_DIFF_THRESHOLD = 30        # per-pixel grey difference that counts

_DIFF_REGION_MIN = 0.02     # a region counts as changed at 2% of its pixels

_DIFF_QUIET = 0.005         # under 0.5% overall reads as "unchanged"



def _virtual_origin(monitors) -> tuple[int, int]:
    """(left, top) of the virtual desktop's origin: the minimum
    monitor left/top (negative when a monitor sits left of or
    above the primary), else (0, 0) when no monitor list is
    available. ImageGrab's all-screens frame starts at this
    origin, so screen-pixel rects translate by it into frame
    pixels."""
    try:
        if monitors:
            return (min(int(m["left"]) for m in monitors),
                    min(int(m["top"]) for m in monitors))
    except (KeyError, TypeError, ValueError):
        pass
    return (0, 0)



def _monitor_rect(monitor: dict) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of one monitor dict."""
    return (int(monitor["left"]), int(monitor["top"]),
            int(monitor["right"]), int(monitor["bottom"]))



def _resolve_capture_monitor(spec):
    """(monitor, error) for capture tools: '' means 'no monitor
    selected — use the tool's default path'. A non-empty spec
    (index, device-name fragment, 'main'/'primary', 'next')
    resolves through _monitors()/_resolve_monitor(); 'next'
    cycles from the foreground window's monitor. Never raises:
    an unreadable monitor list or an unknown spec is an honest
    error string, never a silent primary fallback."""
    text = (spec or "").strip() if isinstance(spec, str) else ""
    if not text:
        return None, ""
    monitors = _monitors()
    if not monitors:
        return None, ("Could not read the monitor list on this PC, "
                      "so a monitor-specific capture is unavailable.")
    current = None
    try:
        fg = _foreground_hwnd()
        current = _monitor_for_window(fg, monitors) if fg else None
    except Exception:  # noqa: BLE001 — fall through to primary
        current = None
    if current is None:
        current = next((m for m in monitors if m["primary"]),
                       monitors[0])
    target, err = _resolve_monitor(text, monitors, current)
    if target is None:
        return None, err
    return target, ""



def _grab_screen_image(monitor_rect=None):
    """(PIL Image, error): the full virtual desktop, RGB — or,
    when monitor_rect (left, top, right, bottom in true screen
    pixels) is given, exactly that monitor's crop of the
    all-screens frame (DESK-01/DISPLAY-01: the shared capture
    seam for screen_diff and the UIA probe path). The crop is
    taken through vision's all-screens grab, translated by the
    virtual origin, so no second grab implementation exists.
    GAPS-19: the no-rect branch is the same seam now (it used
    to grab the primary monitor only); both branches go through
    the capture owner via _grab_image."""
    if monitor_rect is not None:
        try:
            from .desktop.vision import _grab_image  # Phase 3 D4c: vision hub moved to core/desktop/
        except Exception as e:  # noqa: BLE001
            return None, (f"Could not capture the screen: {e}. "
                          "Screen capture needs a display — it is "
                          "unavailable on a headless system.")
        try:
            ox, oy = _virtual_origin(_monitors())
            left, top, right, bottom = (int(v) for v in monitor_rect)
            frame = _grab_image(bbox=(left - ox, top - oy,
                                      right - ox, bottom - oy))
        except Exception as e:  # noqa: BLE001 — headless / no display
            return None, (f"Could not capture the screen: {e}. "
                          "Screen capture needs a display — it is "
                          "unavailable on a headless system.")
        return frame.convert("RGB"), None
    try:
        from .desktop.vision import _grab_image
    except Exception as e:  # noqa: BLE001
        return None, (f"Could not capture the screen: {e}. Screen "
                      "capture needs a display — it is unavailable on "
                      "a headless system.")
    try:
        image = _grab_image()
    except Exception as e:  # noqa: BLE001 — headless / no display
        return None, (f"Could not capture the screen: {e}. Screen "
                      "capture needs a display — it is unavailable on "
                      "a headless system.")
    return image.convert("RGB"), None



# --- screen_diff baseline monitor record (DESK-01) -------------------------
# A baseline saved for one monitor must never be silently compared
# against another monitor's picture. The sidecar records which
# monitor the PNG belongs to; a baseline saved before sidecars
# existed has no sidecar and is treated as "monitor unknown"
# (the primary screen's picture, as _grab_screen_image used to
# capture) — an explicit-monitor compare against it refuses
# honestly instead of comparing unlike pictures.

_BASELINE_SIDECAR_NAME = "screen_diff_baseline.json"



def _baseline_sidecar_path():
    return _DATA_DIR / _BASELINE_SIDECAR_NAME



def _write_baseline_sidecar(monitor) -> None:
    """Record a monitor dict beside the baseline PNG. Best-effort:
    a failed write must not sink the baseline save itself."""
    try:
        import json

        record = {
            "index": int(monitor["index"]),
            "device": str(monitor["device"]),
            "left": int(monitor["left"]), "top": int(monitor["top"]),
            "right": int(monitor["right"]),
            "bottom": int(monitor["bottom"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }
        path = _baseline_sidecar_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
    except Exception:  # noqa: BLE001 — best effort only
        pass



def _read_baseline_sidecar():
    """(record, corrupt): the monitor record for the saved
    baseline, or (None, False) when no sidecar exists (a legacy
    baseline), or (None, True) when a sidecar exists but cannot
    be read — a corrupt record refuses the compare, it never
    falls back to an assumed monitor."""
    path = _baseline_sidecar_path()
    if not path.exists():
        return None, False
    try:
        import json

        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict) or "index" not in record:
            return None, True
        return record, False
    except Exception:  # noqa: BLE001
        return None, True



def _monitor_sentence(monitor) -> str:
    """' (monitor 2: \\\\DISPLAY2, 1920×1080)' for result text."""
    return (f" (monitor {monitor['index']}: {monitor['device']}, "
            f"{monitor['width']}×{monitor['height']})")



def _pct(fraction: float) -> str:
    return f"{max(1, round(fraction * 100))}%"



def screen_diff(action: str = "compare", monitor: str = "") -> str:
    """Save a baseline screenshot, or describe what changed since it.

    DESK-01: `monitor` (index, device-name fragment, 'main' /
    'primary', 'next') scopes both the baseline and the compare
    to that monitor's crop of the all-screens frame, resolved
    through _resolve_capture_monitor; without it the tool keeps
    today's primary-screen behaviour byte-for-byte. A baseline
    is never compared against a different monitor's picture: a
    sidecar records the baseline's monitor, and a mismatched,
    legacy (sidecar-less), or corrupt-record compare refuses
    honestly instead of comparing unlike pictures."""
    act = (action or "compare").strip().lower()
    if act not in ("compare", "save_baseline"):
        return (f"Unknown screen_diff action: '{action}'. Use "
                "'save_baseline' to store the picture, or 'compare' "
                "(the default) to describe what changed since.")
    target, mon_err = _resolve_capture_monitor(monitor)
    if mon_err:
        return mon_err
    rect = _monitor_rect(target) if target is not None else None
    mon_note = _monitor_sentence(target) if target is not None else ""
    baseline_path = _DATA_DIR / _BASELINE_NAME
    if act == "save_baseline":
        image, err = _grab_screen_image(rect)
        if err:
            return err
        try:
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            image.save(baseline_path, format="PNG")
        except Exception as e:  # noqa: BLE001
            return f"Captured the screen but could not save the baseline: {e}"
        if target is not None:
            _write_baseline_sidecar(target)
        else:
            # A no-monitor save supersedes any monitor baseline:
            # leaving the old sidecar would wrongly constrain
            # later compares to a monitor this picture is not.
            try:
                _baseline_sidecar_path().unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 — best effort
                pass
        return (f"Saved the baseline screen picture{mon_note} "
                f"({image.width}×{image.height}) to {baseline_path}. "
                "Run screen_diff again later and I'll describe what "
                "changed.")
    if not baseline_path.exists():
        return ("No baseline picture is saved yet, so there's nothing "
                "to compare against. Run screen_diff with action "
                "'save_baseline' first, then compare later.")
    sidecar, corrupt = _read_baseline_sidecar()
    if corrupt:
        return ("The saved baseline's monitor record couldn't be "
                "read, so I can't tell which screen it shows — save "
                "a fresh baseline with action 'save_baseline' before "
                "comparing.")
    if sidecar is not None:
        if target is None:
            return (f"The saved baseline is monitor "
                    f"{sidecar['index']}'s picture "
                    f"({sidecar.get('device', '?')}), not the whole "
                    "screen — pass monitor "
                    f"'{sidecar['index']}' to compare against it, "
                    "or save a fresh baseline without a monitor.")
        if int(sidecar["index"]) != int(target["index"]):
            return (f"The saved baseline is monitor "
                    f"{sidecar['index']}'s picture "
                    f"({sidecar.get('device', '?')}), but this "
                    f"compare asked for monitor {target['index']} "
                    f"({target['device']}) — comparing unlike "
                    "pictures would mislead, so nothing was "
                    "compared. Save a fresh baseline for monitor "
                    f"{target['index']} first.")
    elif target is not None:
        return ("The saved baseline predates monitor tracking — it "
                "is the primary screen's picture, not monitor "
                f"{target['index']}'s. Save a fresh baseline for "
                f"monitor {target['index']} first, then compare.")
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError:
        return ("Comparing screens isn't available in this build of "
                "VYUK — the image part didn't make it into the package.")
    try:
        baseline = Image.open(baseline_path).convert("RGB")
    except Exception as e:  # noqa: BLE001
        return (f"The saved baseline picture couldn't be read ({e}) — "
                "save a fresh one with action 'save_baseline'.")
    current, err = _grab_screen_image(rect)
    if err:
        return err
    size_note = ""
    if current.size != baseline.size:
        size_note = (f" (The screen size changed since the baseline — "
                     f"{baseline.width}×{baseline.height} then, "
                     f"{current.width}×{current.height} now — so I "
                     "compared them at the baseline's size.)")
        current = current.resize(baseline.size)
    # Work small: a 480-wide greyscale pair is plenty for a coarse
    # "what changed where" answer and keeps the stats cheap.
    scale = min(1.0, 480 / baseline.width)
    small_size = (max(3, int(baseline.width * scale)),
                  max(3, int(baseline.height * scale)))
    base_small = baseline.convert("L").resize(small_size)
    cur_small = current.convert("L").resize(small_size)
    diff = ImageChops.difference(base_small, cur_small)
    mask = diff.point(lambda p: 255 if p > _DIFF_THRESHOLD else 0)
    overall = ImageStat.Stat(mask).mean[0] / 255.0
    if overall < _DIFF_QUIET:
        return ("The screen looks essentially unchanged since the "
                f"baseline (under {_pct(_DIFF_QUIET)} of pixels "
                f"differ).{mon_note}{size_note} This is a coarse "
                "check — a one-line text edit might not register.")
    w, h = small_size
    cells = []
    for row in range(3):
        for col in range(3):
            box = (col * w // 3, row * h // 3,
                   (col + 1) * w // 3, (row + 1) * h // 3)
            frac = ImageStat.Stat(mask.crop(box)).mean[0] / 255.0
            if frac >= _DIFF_REGION_MIN:
                cells.append((_DIFF_REGIONS[row][col], frac))
    cells.sort(key=lambda c: -c[1])
    if cells:
        biggest = ", ".join(f"{name} (~{_pct(frac)})"
                            for name, frac in cells[:3])
        rest = cells[3:]
        rest_note = (" Smaller changes in: "
                     + ", ".join(name for name, _f in rest) + "."
                     ) if rest else ""
        where = f"Biggest changes: {biggest}.{rest_note}"
    else:
        where = ("The change is spread thinly everywhere rather than "
                 "concentrated in one area.")
    return (f"About {_pct(overall)} of the screen changed since the "
            f"baseline.{mon_note} {where}{size_note} This is a coarse "
            "pixel comparison — it says where the picture changed, "
            "not what the new content means.")



# ---------------------------------------------------------------------------
# FOCUS WINDOW BY TEXT — find a window by what's written inside it
# ---------------------------------------------------------------------------


def _grab_desktop_frame():
    """(PIL Image, error): one all-screens desktop frame via
    vision's all-screens grab — the single frame the focus
    search crops per candidate rect, so no candidate is ever
    brought forward just to be read (DESK-03)."""
    try:
        from .desktop.vision import _grab_image  # Phase 3 D4c: vision hub moved to core/desktop/

        frame = _grab_image()
    except Exception as e:  # noqa: BLE001 — headless / no display
        return None, (f"Could not capture the screen: {e}. Screen "
                      "capture needs a display — it is unavailable on "
                      "a headless system.")
    return frame.convert("RGB"), None



def _ocr_words_in_rect(frame, rect, monitors=None):
    """(words, error): OCR words inside one screen rect, read
    from an already-grabbed desktop frame — never by bringing
    the window forward. The crop is translated by the virtual
    origin and OCR'd through control's native pipeline with the
    rect's origin as the crop meta, so boxes come back in true
    screen pixels, exactly like _ocr_words' boxes. Never raises;
    an unavailable OCR engine is an honest error string."""
    if frame is None:
        return None, "Could not capture the screen to read its text."
    try:
        ox, oy = _virtual_origin(monitors)
        left, top, right, bottom = (int(v) for v in rect)
        box = (max(0, left - ox), max(0, top - oy),
               min(frame.width, right - ox),
               min(frame.height, bottom - oy))
        if box[2] <= box[0] or box[3] <= box[1]:
            return [], None
        crop = frame.crop(box)
    except Exception as e:  # noqa: BLE001
        return None, f"OCR failed: {e}"
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        return None, ("Text reading isn't available in this build of "
                      "VYUK — the OCR part didn't make it into the "
                      "package.")
    except Exception as e:  # noqa: BLE001
        return None, (f"OCR is unavailable on this PC: pytesseract "
                      f"could not be loaded ({e}).")
    try:
        from .voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/

        if not local_assets.configure_pytesseract(pytesseract):
            return None, ("Text reading isn't set up on this machine "
                          "yet — the OCR engine that comes with VYUK "
                          "isn't in place. It arrives with the "
                          "installer; nothing for you to download.")
        from .desktop.control import _ocr_image_words  # Phase 3 D4a: control hub moved to core/desktop/

        words, _note = _ocr_image_words(
            crop, {"crop_left": left, "crop_top": top},
            pytesseract, Output)
    except Exception as e:  # noqa: BLE001 — the seam must not raise
        return None, f"OCR failed: {e}"
    return words, None



def _restore_foreground(user32, original) -> None:
    """Put the original foreground window back, best-effort —
    only when the foreground actually moved. Called on every
    non-success path of the focus search (DESK-03), so a search
    never leaves the user's foreground on a candidate it merely
    inspected. Never raises."""
    if original is None:
        return
    try:
        if user32.GetForegroundWindow() != original:
            user32.SetForegroundWindow(original)
    except Exception:  # noqa: BLE001 — best effort
        pass

# SKILLS SPLIT PHASE A BATCH 2 INTENTIONAL (cycle-free cross-import):
# capture and window-manip import each other, so every module's own
# definitions precede its sibling imports — this import sits at the
# bottom, after all definitions, so importing in any order
# (facade-first or standalone) works.
from .skills_hands3_window_manip import (
    _foreground_hwnd,
    _monitor_for_window,
    _monitors,
    _resolve_monitor,
)
