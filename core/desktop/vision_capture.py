"""Capture/geometry/marks helpers for the vision engine (skills split B16).

Pure helpers moved verbatim from core/desktop/vision.py. This module
never touches the vision_manager singleton (that dependency lives in
vision_manager.py), so the dependency direction stays one-way:
vision_manager -> vision_capture -> facade.
"""

import io

# GAPS-19: Image/ImageGrab are no longer read by this module —
# _grab_image delegates to the capture owner — but the names stay
# bound: core/desktop/vision.py re-exports them (vision.Image /
# vision.ImageGrab), and suites patch vision.ImageGrab as the
# legacy grab seam. Removing the import would break both.
from PIL import Image, ImageGrab  # noqa: F401 — re-exported by vision.py

# v5.45 (P-3): the pure geometry helper _fit_within MOVED to
# core/platform/windows.py (the platform-backend seam) and is
# re-exported here, so the legacy import path
# (core.desktop.vision._fit_within) returns the very same callable the
# backend exposes. Its body is pure — the move is mechanical,
# nothing else in this module moved.
from ..platform.windows import _fit_within  # noqa: F401 — Phase 3 D4c: file now in core/desktop/; platform stays flat in core/

# Grounding captures (v5.43, E10): the capture that feeds
# take_screenshot / cursor grounding is taken at this max_dim
# instead of the 1024 default, so small buttons survive the
# downscale. 1568 is the grounding size vision models are
# commonly tuned for; ordinary captures keep the 1024 default.
GROUNDING_MAX_DIM = 1568


def _routed_backend():
    """v5.50 (programme items 41/42): when a NON-Windows platform
    backend serves this process — the scripted FakeBackend under
    VYUK_PLATFORM=fake, or an EXPERIMENTAL native backend
    (darwin/linux) — the backend supplies this module's RAW
    frames and geometry probes (foreground rect / virtual
    bounds). It does NOT replace the capture pipeline: the
    denylist gate, the foreground crop, the cursor / marks
    drawing, the geometry record and the JPEG encode are
    vision-layer policy, platform-independent, and run in this
    module identically for backend-grabbed and legacy-grabbed
    frames (reconciliation R5-V — the original items-41/42
    delegation sat ABOVE the pipeline and silently dropped all
    of that policy on backend hosts). This module's legacy
    bodies ARE the Windows implementation (the WindowsBackend
    delegates back into them), so when the Windows backend
    serves (backend.name == "windows") this returns None and
    every function runs its pre-v5.50 body unchanged —
    Windows behaviour is byte-identical. Resolved lazily at call
    time, like the WindowsBackend's own delegation."""
    try:
        from ..platform import get_backend  # Phase 3 D4c: vision hub moved to core/desktop/

        backend = get_backend()
    except Exception:  # noqa: BLE001 — seam failure = legacy path
        return None
    return backend if backend.name != "windows" else None


def _backend_raw_full_frame(backend):
    """(image, meta) of the serving backend's RAW full-screen
    frame — capture_native_image(full_screen=True), the one
    capture surface the routed delegation still uses — or None
    when the backend cannot grab. A backend that cannot grab
    says so with its documented fail-soft RuntimeError ("no
    capture seam or OS grab could run" — core/platform/_seamed.py
    and the ABC contract); that means the backend captured
    NOTHING, so the caller falls through to this module's legacy
    grab path (R4's fall-through, kept). A backend that CAN grab
    returns its frame here and is never bypassed. Only
    RuntimeError is treated as "no routed capture": any other
    failure is the grab's own, exactly as the legacy path would
    surface it."""
    try:
        image, meta = backend.capture_native_image(full_screen=True)
    except RuntimeError:
        return None
    if image is None:
        return None
    return image, meta


def _cursor_position():
    """(x, y) of the mouse cursor in screen pixels, or None
    when no source can say. Windows: user32. Elsewhere: the
    routed backend (below)."""
    try:
        import ctypes

        class _POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = _POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
            return int(pt.x), int(pt.y)
    except Exception:  # noqa: BLE001 — not Windows / no user32
        pass
    # v5.50 R5-V: last resort, the routed backend's own cursor
    # report — every backend phrases a known position as "Cursor
    # is at (x, y) …" (the shape core/desktop/cursor.py's reader has
    # always used), and a scripted/seamed backend may know the
    # cursor when this host's OS sources cannot say (the
    # FakeBackend's scripted cursor, a seamed input provider).
    # Unknown stays None: an unreadable report parses to
    # nothing, never a guessed point.
    backend = _routed_backend()
    if backend is not None:
        try:
            import re

            m = re.search(r"Cursor is at \((-?\d+), (-?\d+)\)",
                          backend.get_cursor_position() or "")
            if m:
                return int(m.group(1)), int(m.group(2))
        except Exception:  # noqa: BLE001 — position is best-effort
            pass
    return None


def foreground_window_rect() -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the foreground window in
    virtual-screen pixels, or None when no window has focus (or
    the platform can't say).

    Reuses the window helpers that already exist — skills_window_control's
    _foreground_hwnd / _window_rect (themselves built on control's
    title reader + GetWindowRect) — with a direct user32 fallback
    of the same two calls (the shape skills_screen_capture.capture_window
    uses). Never raises: "unknown" is a normal answer, and every
    caller treats it as "capture the full desktop"."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.foreground_window_rect()
    try:
        from ..skills_window_control import _foreground_hwnd, _window_rect  # Phase 3 D4c: vision hub moved to core/desktop/

        hwnd = _foreground_hwnd()
        if hwnd:
            rect = _window_rect(hwnd)
            if rect is not None:
                left, top, right, bottom = (int(v) for v in rect)
                if right > left and bottom > top:
                    return (left, top, right, bottom)
            return None
    except Exception:  # noqa: BLE001 — fall through to user32
        pass
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        left, top = int(rect.left), int(rect.top)
        right, bottom = int(rect.right), int(rect.bottom)
        if right <= left or bottom <= top:
            return None
        return (left, top, right, bottom)
    except Exception:  # noqa: BLE001 — not Windows / no user32
        return None


def _record_virtual_bounds():
    """Virtual bounds for the capture record (2026-11-03, norm1000
    freshness): the same probe the dispatch check compares against.
    Never raises: unmeasurable means None, and the check skips."""
    try:
        bounds = _virtual_screen_bounds()
    except Exception:  # noqa: BLE001 — unmeasurable, record None
        return None
    if not bounds:
        return None
    try:
        return tuple(int(v) for v in bounds)
    except (TypeError, ValueError):
        return None


def _virtual_screen_bounds() -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the whole virtual desktop via
    skills_screen_capture's probe (system metrics on Windows, a full grab's
    size elsewhere), or None when it can't be determined."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        return _backend.virtual_screen_bounds()
    try:
        from ..skills_screen_capture import _virtual_screen  # Phase 3 D4c: vision hub moved to core/desktop/

        bounds = _virtual_screen()
    except Exception:  # noqa: BLE001
        return None
    if not bounds:
        return None
    try:
        return tuple(int(v) for v in bounds)
    except (TypeError, ValueError):
        return None


def _capture_block_reason() -> str | None:
    """v5.48 (programme item 21): the sensitive-window denylist
    check (core/capture_guard.foreground_capture_block) every
    capture entry in this module runs FIRST. Returns the honest
    refusal reason when the foreground window is denylisted, else
    None. The guard itself never raises; this wrapper keeps even
    an import failure of the guard from breaking captures on
    exotic installs (the guard ships in-package, so this is
    belt-and-braces only). NOTE: this is the denylist ONLY — the
    per-session capture ASK (capture_guard.ensure_capture_allowed)
    deliberately does NOT live here: the plain screenshot look is
    Tier-0 observe and must never gain an ask, and screen-watch
    polls must never pop a prompt. The ask belongs to the
    camera / microphone / screenshot-save handlers."""
    try:
        from .. import capture_guard  # Phase 3 D4c: vision hub moved to core/desktop/

        return capture_guard.foreground_capture_block()
    except Exception:  # noqa: BLE001 — see docstring
        return None


def _grab_image(bbox=None):
    """One grab through the capture owner
    (``core/desktop/screen_capture.py`` ``grab_virtual``): every
    grab asks for every screen (a window on a secondary monitor
    is real pixels too), with the owner's validation, timestamp
    and honest failure. ``bbox`` is (left, top, right, bottom)
    in virtual-screen pixels; None is the whole virtual
    desktop. Raises RuntimeError carrying the owner's reason
    when the grab fails — capture failure is the caller's
    sentence to phrase.

    Desktop programme F-05: the no-bbox branch used to call
    plain ImageGrab.grab(), so the legacy whole-desktop path
    silently captured the primary screen only while the bbox
    branch captured all screens — the same fallback now covers
    the same desktop scope with or without a bounding box.
    GAPS-19: the grab itself moved to the owner; this function
    is the vision seam over it (callers + tests keep patching
    ``vision._grab_image``, never a display)."""
    from .screen_capture import frame_to_pil, grab_virtual

    got = grab_virtual(bbox=bbox)
    if not got["ok"]:
        raise RuntimeError(got["reason"])
    return frame_to_pil(got["frame"])


# ---------------------------------------------------------------------------
# Set-of-marks grounding (v5.49, programme item 30)
# ---------------------------------------------------------------------------
# A grounding capture (capture_screen with marks=True) enumerates
# the foreground window's interactive controls via core/desktop/uia.py's
# marks table (the natural home — the marks are UIA facts), draws
# each mark's numbered box on the frame BEFORE it is thumbnailed,
# and records the table so tools_handlers.py can ship the id->control
# table alongside the image and click_by_mark can resolve an id.
# OCR word boxes join the table only when UIA yields few controls
# (a canvas / custom-drawn surface): the words come from THIS
# frame via core/desktop/control.py's native OCR pipeline, never a second
# capture. The Phase-3 denylist needs no new check here:
# capture_native_image (which every capture funnels through)
# already refuses a denylisted foreground before any pixels are
# grabbed, so a blocked capture raises before enumeration and the
# marks table is cleared, never left stale.

#: Below this many UIA marks, OCR word boxes are merged in.
_MARKS_OCR_THRESHOLD = 3


def draw_marks_on_image(image, marks, origin=(0, 0)):
    """Draw each mark's numbered box on `image` (a PIL Image) and
    return the (possibly mode-converted) image.

    Extends the single-point marker style of capture_screen's
    `mark=` ring: a red rectangle per mark in TRUE screen pixels,
    translated by `origin` (the capture's crop origin) into image
    coordinates, with a filled red chip carrying the mark's id at
    the box's top-left corner. Marks fully outside the image are
    skipped. Never raises — a drawing failure returns the image
    untouched, since a marker must never sink a capture."""
    try:
        if not marks:
            return image
        from PIL import ImageDraw

        if image.mode != "RGB":
            image = image.convert("RGB")
        draw = ImageDraw.Draw(image)
        ox, oy = int(origin[0]), int(origin[1])
        width = max(2, image.width // 320)
        for mark in marks:
            try:
                left, top, right, bottom = (int(v) for v in mark["rect"])
            except (KeyError, TypeError, ValueError):
                continue
            left, top = left - ox, top - oy
            right, bottom = right - ox, bottom - oy
            if right < 0 or bottom < 0 or left >= image.width \
                    or top >= image.height:
                continue
            draw.rectangle((left, top, right, bottom),
                           outline=(255, 0, 0), width=width)
            label = str(mark.get("id", "?"))
            chip_w = 8 + 7 * len(label)
            chip_h = 14
            chip_top = top if top + chip_h <= image.height else max(0, bottom - chip_h)
            draw.rectangle((left, chip_top, left + chip_w, chip_top + chip_h),
                           fill=(255, 0, 0))
            draw.text((left + 4, chip_top + 3), label, fill=(255, 255, 255))
        return image
    except Exception:  # noqa: BLE001 — see docstring
        return image


def _ocr_words_for_image(image, meta):
    """OCR word boxes for an already-captured native frame via
    core/desktop/control.py's pipeline (_ocr_image_words: x2 upscale +
    binarisation, resolved Tesseract config/lang), or None when
    OCR can't run here. Never raises.

    Command Deck Phase F: the OCR pass brackets a CaptureStarted /
    CaptureFinished pair with scope "ocr" — the overlay's scan
    sweep follows exactly this bracket. The pair is emitted even
    when OCR itself is unavailable, so the sweep cannot strand
    on; the emits are cosmetic and never raise.
    """
    try:
        from .. import state_bus_producers as _prod  # Phase 3 D4c: vision hub moved to core/desktop/

        _prod.emit_capture_started("ocr")
    except Exception:  # noqa: BLE001 — cosmetic channel, never break
        pass
    try:
        import pytesseract
        from pytesseract import Output

        from . import control as _control  # Phase 3 D4a: control hub moved to core/desktop/ (D4c: vision now in core/desktop/, .desktop segment dropped)
        from ..voice import local_assets  # Phase 3 V2: local_assets moved to core/voice/ (D4c: vision now in core/desktop/, one more level up)

        if not local_assets.configure_pytesseract(pytesseract):
            return None
        words, _note = _control._ocr_image_words(image, meta,
                                                  pytesseract, Output)
        return words
    except Exception:  # noqa: BLE001 — OCR is a fallback, not a gate
        return None
    finally:
        try:
            from .. import state_bus_producers as _prod  # Phase 3 D4c: vision hub moved to core/desktop/

            _prod.emit_capture_finished("ocr")
        except Exception:  # noqa: BLE001 — cosmetic channel, never break
            pass


def ground_marks_for_frame(image, meta) -> list:
    """The marks table for one native frame: UIA enumeration, with
    OCR word boxes from THIS frame merged in when UIA yielded
    fewer than _MARKS_OCR_THRESHOLD controls. Also records the
    table as core/desktop/uia.py's last grounding table. [] on any
    failure. Never raises."""
    try:
        from . import uia as _uia  # Phase 3 D4b: uia hub moved to core/desktop/ (D4c: vision now in core/desktop/, .desktop segment dropped)

        marks, _note = _uia.enumerate_marks()
    except Exception:  # noqa: BLE001
        return []
    if len(marks) < _MARKS_OCR_THRESHOLD:
        words = _ocr_words_for_image(image, meta)
        if words:
            try:
                from . import uia as _uia  # Phase 3 D4b: uia hub moved to core/desktop/ (D4c: vision now in core/desktop/, .desktop segment dropped)

                marks = _uia.merge_ocr_marks(marks, words)
                _uia._set_last_marks(marks, _uia.last_marks_foreground())
            except Exception:  # noqa: BLE001 — UIA marks still stand
                pass
    # Conversion pass (Wave 5): best-effort grounding fusion —
    # the UIA-first fuse of this marks table, recorded (hashes
    # only) in core/w5_wiring.py. Never changes the table.
    try:
        from .. import w5_wiring as _w5  # Phase 3 D4c: vision hub moved to core/desktop/

        _w5.note_grounding_fusion(marks, image)
    except Exception:  # noqa: BLE001 — fusion is best-effort only
        pass
    return marks


def _w5_note_capture(image):
    """Conversion pass (Wave 5): best-effort capture-evidence hook.

    Notes the Wave 5 ladder route + frame hash (hashes only, no
    frame bytes stored) via core/w5_wiring.py, AFTER the denylist
    + PF-17 gates above have passed. Returns the image unchanged
    on any failure — a wiring fault can never sink a capture.
    """
    try:
        from .. import w5_wiring as _w5  # Phase 3 D4c: vision hub moved to core/desktop/

        return _w5.note_capture_image(image)
    except Exception:  # noqa: BLE001 — evidence is best-effort only
        return image


