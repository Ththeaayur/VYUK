"""
Eyes skills pack II for VYUK (v5.11) — verified screen capture: a
screen region (with an optional destination path), one window by
title (restored first if minimized, never a guessed first match),
and a single-pixel colour probe.

Same rules as core/skills_media.py (the pack this one extends):
plain functions returning human-readable strings, never raising;
platform-specific dependencies (PIL, ctypes.windll) are imported
lazily inside the function that needs them and guarded, so this
module imports cleanly on any OS.

House discipline carried over from the v5.6–v5.9 control work:
- Captures are verified, not asserted: the saved file is read back
  with PIL and the result reports the size that actually landed.
- Nothing is overwritten silently: an explicit path that already
  exists gets a suffixed sibling and the result names the real file.
- Ambiguous window titles are never guessed (v5.9 no-guess rule):
  several matches are listed and nothing is captured.
- Regions are clamped to the virtual screen when its bounds can be
  determined, and any trimming is stated in the result; where the
  bounds can't be determined, the result says an out-of-screen part
  would come out black instead of pretending precision.

Wiring: the parent registers this pack via register_all(registry) —
this module never touches the registry singleton itself.
"""

import sys
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_NOT_WINDOWS = "this system is not Windows, so nothing was changed."

_MAX_DIMENSION = 8000
_MAX_COORD = 32768

_SAVE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".bmp": "BMP",
    ".webp": "WEBP",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _virtual_screen():
    """(left, top, right, bottom) of the whole desktop, or None.

    Windows answers cheaply and exactly via the virtual-screen
    metrics (the origin is negative when a monitor sits left of or
    above the primary). Elsewhere the capture owner's bounds probe
    answers (GAPS-19: no full grab just to measure the screen).
    None means "couldn't determine" (usually: no display at all),
    and the caller decides what honesty requires then.
    """
    if _is_windows():
        try:
            import ctypes

            user32 = ctypes.windll.user32
            left = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            top = user32.GetSystemMetrics(77)    # SM_YVIRTUALSCREEN
            width = user32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            if width > 0 and height > 0:
                return (left, top, left + width, top + height)
        except Exception:  # noqa: BLE001 — fall through to the owner probe
            pass
    from .desktop.screen_capture import virtual_screen_bounds

    return virtual_screen_bounds()


def _prepare_box(x: int, y: int, width: int, height: int):
    """Clamp a requested box to the virtual screen.

    Returns (box, note) where box is (left, top, right, bottom), or
    (None, error_string) when the box doesn't intersect the screen
    at all. `note` is "" when nothing was trimmed, else a clause for
    the result string; when the screen size couldn't be determined
    the box passes through unclamped and the note carries the
    captures-black caveat instead.
    """
    bounds = _virtual_screen()
    if bounds is None:
        return (x, y, x + width, y + height), (
            " The screen size couldn't be determined, so nothing was "
            "trimmed — any part of the region past the screen's edge "
            "comes out black.")
    left, top, right, bottom = bounds
    clamped = (max(x, left), max(y, top),
               min(x + width, right), min(y + height, bottom))
    if clamped[2] <= clamped[0] or clamped[3] <= clamped[1]:
        return None, (
            f"That region ({x}, {y}, {width}×{height}) lies entirely "
            f"outside the screen, which spans ({left}, {top}) to "
            f"({right}, {bottom}).")
    if clamped != (x, y, x + width, y + height):
        return clamped, (
            f" The region ran past the edge of the "
            f"{right - left}×{bottom - top} screen, so it was trimmed "
            f"to fit.")
    return clamped, ""


def _destination(path: str, kind: str) -> Path | str:
    """Where a capture lands: the explicit path (never clobbering —
    a taken name gets a _1, _2… suffix) or the house default,
    data/screenshots/screenshot_<kind>_<timestamp>.png."""
    if path and str(path).strip():
        dest = Path(str(path)).expanduser()
        if not dest.is_absolute():
            dest = (Path.cwd() / dest)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return f"Could not create the folder {dest.parent}: {e}"
        if dest.exists():
            counter = 1
            while True:
                candidate = dest.with_name(f"{dest.stem}_{counter}{dest.suffix}")
                if not candidate.exists():
                    dest = candidate
                    break
                counter += 1
        return dest
    out_dir = _DATA_DIR / "screenshots"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"Could not create the screenshots folder {out_dir}: {e}"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = out_dir / f"screenshot_{kind}_{stamp}.png"
    counter = 1
    while dest.exists():
        dest = out_dir / f"screenshot_{kind}_{stamp}_{counter}.png"
        counter += 1
    return dest


def _grab_and_save(box, path: str, kind: str):
    """Grab `box`, save it, read the file back to verify.

    Returns (dest, (width, height), file_bytes) on success or an
    error string. The reported size is the one read back from the
    saved file, never just the size that was asked for. The grab
    goes through the capture owner (GAPS-19): `box` is a
    virtual-screen bbox, served by DXcam or the ImageGrab
    fallback, validated by the owner and never silently clamped.
    """
    from .desktop.screen_capture import frame_to_pil, grab_virtual

    got = grab_virtual(bbox=box)
    if not got["ok"]:
        return (f"Could not capture the screen: {got['reason']}. "
                "Screen capture needs a display — it is unavailable "
                "on a headless system.")
    try:
        image = frame_to_pil(got["frame"])
    except ValueError as e:
        return (f"Could not capture the screen: {e}. "
                "Screen capture needs a display — it is unavailable "
                "on a headless system.")
    dest = _destination(path, kind)
    if isinstance(dest, str):
        return dest
    fmt = _SAVE_FORMATS.get(dest.suffix.lower(), "PNG")
    try:
        to_save = image.convert("RGB") if fmt in ("JPEG", "BMP") else image
        to_save.save(dest, fmt)
    except Exception as e:  # noqa: BLE001
        return f"Could not save the screenshot to {dest}: {e}"
    try:
        from PIL import Image

        with Image.open(dest) as saved:
            actual_size = saved.size
        file_bytes = dest.stat().st_size
    except Exception as e:  # noqa: BLE001
        return f"The screenshot was saved to {dest} but couldn't be read back to verify it: {e}"
    return dest, actual_size, file_bytes


def _coerce_int(value, label: str):
    """int(value) or an error string naming the field."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return f"A screenshot needs whole numbers — {label} wasn't one: {value!r}."


# ---------------------------------------------------------------------------
# SCREENSHOT — a region, to a chosen path if asked
# ---------------------------------------------------------------------------


def screenshot_region(x: int, y: int, width: int, height: int, path: str = "") -> str:
    """Capture a screen rectangle, optionally to an explicit path."""
    coords = []
    for value, label in ((x, "x"), (y, "y"), (width, "width"), (height, "height")):
        coerced = _coerce_int(value, label)
        if isinstance(coerced, str):
            return coerced
        coords.append(coerced)
    x, y, width, height = coords
    if not (1 <= width <= _MAX_DIMENSION and 1 <= height <= _MAX_DIMENSION):
        return (f"The region must be between 1×1 and "
                f"{_MAX_DIMENSION}×{_MAX_DIMENSION} pixels — "
                f"{width}×{height} is outside that range.")
    if not (-_MAX_COORD <= x <= _MAX_COORD and -_MAX_COORD <= y <= _MAX_COORD):
        return (f"That corner ({x}, {y}) is implausibly far from the "
                "screen — coordinates beyond ±32768 pixels aren't sane.")
    box, note = _prepare_box(x, y, width, height)
    if box is None:
        return note
    result = _grab_and_save(box, path, "region")
    if isinstance(result, str):
        return result
    dest, (actual_w, actual_h), file_bytes = result
    return (f"Captured the region at ({x}, {y}), {width}×{height}: {dest} "
            f"({actual_w}×{actual_h}, {_human_size(file_bytes)}).{note}")


# ---------------------------------------------------------------------------
# SCREENSHOT — one window, restored first if minimized
# ---------------------------------------------------------------------------


def _pick_window(windows, title: str):
    """Choose ONE window for `title` from (hwnd, title) pairs.

    Returns (match, candidates): match is the single (hwnd, title)
    the caller may act on, or None. An exact title (case-insensitive)
    beats substring matches — the v5.9 discipline: a unique exact
    match proceeds even when other windows merely contain the text;
    anything else with more than one candidate comes back as a list
    for the caller to refuse with, never a silent first pick.
    """
    needle = (title or "").strip().lower()
    if not needle:
        return None, []
    exact = [(h, t) for h, t in windows if t.strip().lower() == needle]
    pool = exact if exact else [(h, t) for h, t in windows if needle in t.lower()]
    if len(pool) == 1:
        return pool[0], pool
    return None, pool


def capture_window(title: str, path: str = "") -> str:
    """Capture one window (found by its title), restoring it first
    if it is minimized."""
    needle = (title or "").strip()
    if not needle:
        return "A window capture needs the window's title (or part of it), e.g. 'Chrome'."
    if not _is_windows():
        return ("Capturing a single window uses Windows window APIs — "
                f"{_NOT_WINDOWS}")
    try:
        import ctypes
        from ctypes import wintypes

        from .desktop.control import _enum_windows  # Phase 3 D4a: control hub moved to core/desktop/

        windows = _enum_windows()
    except Exception as e:  # noqa: BLE001
        return f"Could not list the open windows: {e}"
    match, candidates = _pick_window(windows, needle)
    if match is None:
        if not candidates:
            return f"No window found with a title containing '{needle}'."
        shown = candidates[:8]
        lines = "\n".join(f"• {t}" for _h, t in shown)
        more = (f"\n…and {len(candidates) - len(shown)} more"
                if len(candidates) > len(shown) else "")
        return (f"More than one window matches '{needle}' — I won't guess "
                f"which one to capture:\n{lines}{more}\n"
                "Ask again with more of the exact title.")
    hwnd, win_title = match
    try:
        user32 = ctypes.windll.user32
        restored = False
        if user32.IsIconic(hwnd):
            # A minimized window has no real rectangle — restore it
            # first (SW_RESTORE), like the window tools do.
            user32.ShowWindow(hwnd, 9)
            restored = True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return f"Could not read the position of '{win_title}'."
    except Exception as e:  # noqa: BLE001
        return f"Could not inspect the window '{win_title}': {e}"
    win_w, win_h = rect.right - rect.left, rect.bottom - rect.top
    if win_w < 1 or win_h < 1:
        state = " even after restoring it" if restored else ""
        return (f"'{win_title}' has no visible area to capture{state} "
                f"(its rectangle is {win_w}×{win_h}).")
    box, note = _prepare_box(rect.left, rect.top, win_w, win_h)
    if box is None:
        return note
    result = _grab_and_save(box, path, "window")
    if isinstance(result, str):
        return result
    dest, (actual_w, actual_h), file_bytes = result
    restored_note = " (It was minimized, so I restored it first.)" if restored else ""
    return (f"Captured the window '{win_title}' at "
            f"({rect.left}, {rect.top}), {win_w}×{win_h}: {dest} "
            f"({actual_w}×{actual_h}, {_human_size(file_bytes)})."
            f"{restored_note}{note}")


# ---------------------------------------------------------------------------
# PIXEL — the colour of one screen point (read, or read + click)
# ---------------------------------------------------------------------------


def _click_at(x: int, y: int):
    """Click the left mouse button at (x, y); None on success, else
    an error string. The point passes through the cursor pack's
    bounds fence (imported, not duplicated — one fence for every
    hand), and the dispatch honours the same failsafe as every
    other hand: while it is tripped, nothing is clicked. Kept as a
    module-level seam so tests can fake the click itself.

    Audit fix Batch 1 (SEC-02) — FAIL CLOSED, in three places
    where the old code failed open:
    * Failsafe consult: the tri-state _failsafe_status is
      consulted (TRIPPED / CLEAR / UNAVAILABLE); an
      unconsultable failsafe refuses, it never reads as
      "clear". The legacy _failsafe_tripped consult is kept
      alongside it and a raise from it refuses too, so a
      consult failure is a refusal on either seam.
    * Fence: a fence that raises, or screen bounds that cannot
      be measured at all, refuses — the old "fence
      unmeasurable: click where asked" fallback to raw
      coordinates is deleted. A fenced point that still lands
      outside the measured bounds refuses rather than being
      silently re-aimed.
    * Input tracking: tracking is ESTABLISHED BEFORE dispatch,
      not swallowed after it. A probe of the release path plus
      the real press registration both run before mouse.click;
      if either raises, nothing is clicked. (Production
      note_input_* never raise by design, so this leg is a
      seam-level guard: if a replaced failsafe module ever
      made tracking fail, the click must not go through
      anyway.) A release failure after a successful click is
      reported honestly instead of returning success.
    """
    try:
        from .desktop.cursor import (_FAILSAFE_MSG, _failsafe_status,
                             _failsafe_tripped, _fence_point,
                             _virtual_bounds)  # Phase 3 D4d: cursor hub moved to core/desktop/
    except Exception as e:  # noqa: BLE001 — no safety consult, no click
        return (f"Could not verify the click safeguards "
                f"({type(e).__name__}) — nothing was clicked.")
    try:
        _fs_state, _fs_reason = _failsafe_status()
    except Exception as exc:  # noqa: BLE001 — consult failure
        return (f"VYUK's failsafe could not be consulted "
                f"({type(exc).__name__}) — the click was refused "
                f"rather than dispatched unverified. Nothing was clicked.")
    if _fs_state == "unavailable":
        return (f"VYUK's failsafe could not be consulted "
                f"({_fs_reason}) — the click was refused rather "
                f"than dispatched unverified. Nothing was clicked.")
    if _fs_state == "tripped":
        return _FAILSAFE_MSG
    try:
        if _failsafe_tripped():
            return _FAILSAFE_MSG
    except Exception as exc:  # noqa: BLE001 — consult failure
        return (f"VYUK's failsafe could not be consulted "
                f"({type(exc).__name__}) — the click was refused "
                f"rather than dispatched unverified. Nothing was clicked.")
    try:
        fx, fy = _fence_point(int(x), int(y))
    except Exception as exc:  # noqa: BLE001 — fence unmeasurable: refuse
        return (f"Could not fence ({x}, {y}) inside the screen "
                f"({type(exc).__name__}) — the click was refused "
                f"rather than dispatched at raw coordinates. "
                f"Nothing was clicked.")
    try:
        bounds = _virtual_bounds()
    except Exception as exc:  # noqa: BLE001 — bounds unmeasurable
        return (f"Could not measure the screen bounds "
                f"({type(exc).__name__}) — the click was refused "
                f"rather than dispatched at raw coordinates. "
                f"Nothing was clicked.")
    if bounds is None:
        return ("Could not measure the screen bounds — the click "
                "was refused rather than dispatched at raw "
                "coordinates. Nothing was clicked.")
    min_x, min_y, max_x, max_y = bounds
    if not (min_x <= fx <= max_x and min_y <= fy <= max_y):
        return (f"The fenced point ({fx}, {fy}) lies outside the "
                f"measured screen bounds — the click was refused "
                f"rather than dispatched elsewhere. Nothing was clicked.")
    # The mouse is driven with the native Windows SendInput API
    # through the cursor hub — the same handle the cursor tools
    # use. Off Windows there is no click engine in this build.
    try:
        from .desktop.cursor_input import _mouse_controller

        mouse, Button, err = _mouse_controller()
        if mouse is None:
            raise RuntimeError(err)
    except Exception as e:  # noqa: BLE001
        return (f"Clicking is unavailable on this system ({e}). "
                "Nothing was clicked.")
    try:
        mouse.position = (fx, fy)
    except Exception as e:  # noqa: BLE001
        return (f"Could not click at ({fx}, {fy}) — no display / "
                f"input access on this system? ({e})")
    # v5.48 (programme item 26): register the click's button
    # with the failsafe for the call's duration (mouse.click
    # presses + releases inside one dispatch); unregister on
    # success — a failed click stays tracked for a trip.
    # Batch 1 SEC-02: this registration, and a probe of the
    # release path, are the tracking-establishment check and
    # run BEFORE dispatch — either raising refuses the click.
    try:
        from . import failsafe

        failsafe.note_input_released(
            "button", "__vyuk_tracking_probe__")
        failsafe.note_input_pressed(
            "button", failsafe.input_ident(Button.left),
            Button.left)
    except Exception as exc:  # noqa: BLE001 — tracking unestablished: refuse
        return (f"Input tracking could not be established "
                f"({type(exc).__name__}) — the click was refused "
                f"rather than dispatched untracked. Nothing was clicked.")
    try:
        mouse.click(Button.left, 1)
    except Exception as e:  # noqa: BLE001
        return (f"Could not click at ({fx}, {fy}) — no display / "
                f"input access on this system? ({e})")
    try:
        from . import failsafe

        failsafe.note_input_released(
            "button", failsafe.input_ident(Button.left))
    except Exception as exc:  # noqa: BLE001 — click landed, say so honestly
        return (f"Clicked at ({fx}, {fy}), but the input-tracking "
                f"release could not be confirmed "
                f"({type(exc).__name__}) — treat the click as "
                f"dispatched, not as cleanly tracked.")
    return None


def get_pixel_color(x: int, y: int, action: str = "read") -> str:
    """Read the colour of the single screen pixel at (x, y).

    action='click' additionally clicks that very pixel (left
    button) after a successful read — the result then confirms the
    click and names the colour clicked. Points outside the screen
    are refused before either happens, exactly as for a read."""
    act = str(action or "read").strip().lower()
    if act not in ("read", "click"):
        return (f"Unknown action: '{action}'. Use 'read' (just report "
                "the colour) or 'click' (report it, then click it).")
    coords = []
    for value, label in ((x, "x"), (y, "y")):
        coerced = _coerce_int(value, label)
        if isinstance(coerced, str):
            return coerced
        coords.append(coerced)
    x, y = coords
    bounds = _virtual_screen()
    if bounds is not None:
        left, top, right, bottom = bounds
        if not (left <= x < right and top <= y < bottom):
            return (f"({x}, {y}) is outside the screen, which spans "
                    f"({left}, {top}) to ({right}, {bottom}) — "
                    "there's no pixel to read there.")
    from .desktop.screen_capture import frame_to_pil, grab_virtual

    got = grab_virtual(bbox=(x, y, x + 1, y + 1))
    if not got["ok"]:
        return (f"Could not read the screen: {got['reason']}. "
                "Reading pixels needs a display — it is unavailable "
                "on a headless system.")
    try:
        image = frame_to_pil(got["frame"])
    except ValueError as e:
        return (f"Could not read the screen: {e}. "
                "Reading pixels needs a display — it is unavailable "
                "on a headless system.")
    try:
        red, green, blue = image.convert("RGB").getpixel((0, 0))
    except Exception as e:  # noqa: BLE001
        return f"Could not read the pixel at ({x}, {y}): {e}"
    if act == "click":
        problem = _click_at(x, y)
        if problem:
            return problem
        return (f"Clicked the #{red:02X}{green:02X}{blue:02X} pixel "
                f"at ({x}, {y}).")
    return (f"The pixel at ({x}, {y}) is "
            f"#{red:02X}{green:02X}{blue:02X} "
            f"(rgb {red}, {green}, {blue}).")


# ---------------------------------------------------------------------------
# Registration — the pack's catalogue (the parent wires this in)
# ---------------------------------------------------------------------------


def register_all(registry) -> None:
    """Register every Eyes II pack tool on a ToolRegistry-like object."""
    # Packaging batch: INTENTIONAL OVERRIDE — screenshot_region
    # was already registered by core/skills_media.py (mega pack,
    # registered earlier). Registry.register() is last-write-
    # wins, so THIS handler wins: it takes an explicit output
    # path, verifies the capture, never overwrites an existing
    # file (suffixed copy instead), and confirms the saved
    # file's actual size. Pinned by tests/test_packaging.py.
    registry.register(
        name="screenshot_region",
        description="Take a verified screenshot of one rectangular part of the screen (top-left corner x, y and width, height in pixels; coordinates may be negative on multi-monitor setups) and save it as an image file — to VYUK's screenshots folder by default, or to the given path (an existing file is never overwritten; a suffixed copy is saved instead). The result confirms the saved file's actual size. Use take_screenshot for the whole screen, capture_window for one window.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Left edge of the region, in pixels from the primary screen's left (may be negative with multiple monitors)"},
                "y": {"type": "integer", "description": "Top edge of the region, in pixels from the primary screen's top (may be negative with multiple monitors)"},
                "width": {"type": "integer", "description": "Width of the region in pixels (1–8000)"},
                "height": {"type": "integer", "description": "Height of the region in pixels (1–8000)"},
                "path": {"type": "string", "description": "Optional file path to save to (default: VYUK's screenshots folder, timestamped)"},
            },
            "required": ["x", "y", "width", "height"],
        },
        handler=screenshot_region,
        override=True,
    )
    registry.register(
        name="capture_window",
        description="Take a verified screenshot of a single window, found by its title (or part of it), and save it as an image file — to VYUK's screenshots folder by default, or to the given path (never overwriting). A minimized window is restored first so it can be captured. If several windows match, they are listed and nothing is captured — ask again with more of the exact title.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "The window's title, or part of it, e.g. 'Notepad'"},
                "path": {"type": "string", "description": "Optional file path to save to (default: VYUK's screenshots folder, timestamped)"},
            },
            "required": ["title"],
        },
        handler=capture_window,
    )
    registry.register(
        name="get_pixel_color",
        description="Read the colour of a single pixel on the screen at (x, y) and report it as a hex value and RGB numbers — a probe for checking what's actually shown at a point (a button's colour, whether a pixel is lit). With action='click' it reads the colour first and then clicks that very pixel (left button), and the result confirms the click and the colour clicked; a point outside the screen is refused, exactly as for a read.",
        parameters={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Horizontal position in pixels from the primary screen's left (may be negative with multiple monitors)"},
                "y": {"type": "integer", "description": "Vertical position in pixels from the primary screen's top (may be negative with multiple monitors)"},
                "action": {"type": "string", "enum": ["read", "click"], "description": "'read' (default) only reports the colour; 'click' also clicks the pixel after reading it — and a click is confirmation-gated at the executor (audit fix Batch 1 SEC-01), never a silent read"},
            },
            "required": ["x", "y"],
        },
        handler=get_pixel_color,
    )
