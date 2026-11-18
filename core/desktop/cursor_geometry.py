"""Geometry + capture-freshness helpers for cursor control.

Split out of core/desktop/cursor.py (B34 part 1 of 2): the \u00a73.6
geometry-freshness family \u2014 DPI awareness, virtual-screen bounds,
norm1000 reference/conversion, the capture-freshness refusal, and the
bounds fence. Pure computation plus guarded platform probes; no
cursor movement happens here.

PATCH SEAM (B16 pattern, documented not changed): _fence_point reads
_virtual_bounds through its own module namespace (see
core/platform/windows.py), and the shipped fencing suite fakes
geometry exactly there. Tests that fake geometry therefore patch
BOTH core.desktop.cursor and core.desktop.cursor_geometry bindings.
"""

import sys  # B34 INTENTIONAL: travels with _is_windows


# ---------------------------------------------------------------------------
# Helpers — platform, DPI, screen geometry
# ---------------------------------------------------------------------------

_DPI_AWARE_DONE = False

# v5.54.1: the takeover abort itself is UNCHANGED (the >60 px
# rule and the failsafe corner still stop the move at once) —
# only the words soften. The old line read as a scolding and
# left the user nowhere to go; the new one names the safety
# rule (so it reads as working as designed, not failing) and
# offers the retry instead of ending the task.
_ABORT_MSG = ("I stopped the move because you took the mouse "
              "— that's the safety rule doing its job. Just "
              "ask and I'll try that move again.")
_TAKEOVER_TOLERANCE_PX = 60
_FAILSAFE_CORNER_PX = 2


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _routed_backend():
    """v5.50 (programme items 41/42): when a NON-Windows platform
    backend serves this process — the scripted FakeBackend under
    VYUK_PLATFORM=fake, or an EXPERIMENTAL native backend
    (darwin/linux) — the cursor surface is the backend's, and the
    public functions below delegate to it. This module's legacy
    bodies ARE the Windows implementation (the WindowsBackend
    delegates back into them), so when the Windows backend serves
    (backend.name == "windows") this returns None and every
    function runs its pre-v5.50 body unchanged — Windows
    behaviour is byte-identical. Resolved lazily at call time,
    like the WindowsBackend's own delegation."""
    try:
        from ..platform import get_backend  # Phase 3 D4d: cursor hub moved to core/desktop/; platform stays flat in core/

        backend = get_backend()
    except Exception:  # noqa: BLE001 — seam failure = legacy path
        return None
    return backend if backend.name != "windows" else None


def _ensure_dpi_aware() -> None:
    """Mark this process DPI-aware (Windows), once, so coordinates are
    TRUE pixels even on 125%/150% scaled displays. No-op elsewhere and
    never raises: worst case we fall back to virtualised coordinates."""
    global _DPI_AWARE_DONE
    if _DPI_AWARE_DONE:
        return
    _DPI_AWARE_DONE = True  # attempt exactly once, whatever happens
    if not _is_windows():
        return
    try:
        import ctypes

        try:
            # Per-monitor awareness (Windows 8.1+): 2 = DPI_AWARENESS_CONTEXT
            # equivalent PROCESS_PER_MONITOR_DPI_AWARE.
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            return
        except Exception:  # noqa: BLE001 — older Windows / shcore missing
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def _screen_size():
    """(width, height) in true pixels, or None if it cannot be known.
    Same source order as display_control('info') in control_plus.py.
    GAPS-19: the fallback measures the virtual desktop through the
    capture owner, not a full grab — cursor coordinates are virtual,
    so the virtual size is the honest measure (the old grab sized
    the primary monitor only)."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        _size = _backend.screen_size()
        if _size is not None:
            return _size
    try:
        from .screen_capture import virtual_screen_bounds

        bounds = virtual_screen_bounds()
        if bounds is not None:
            width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
            if width > 0 and height > 0:
                return int(width), int(height)
    except Exception:  # noqa: BLE001 — headless / owner unavailable
        pass
    if _is_windows():
        try:
            import ctypes

            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:  # noqa: BLE001
            pass
    return None


# v5.45 (P-3): the pure geometry helpers _clamp / _to_pixels /
# _norm1000_to_pixels MOVED to core/platform/windows.py (the
# platform-backend seam) and are re-exported here, so every
# legacy import path (core.desktop.cursor._clamp etc.) returns the very
# same callables the backend exposes. Their bodies are pure —
# the move is mechanical, nothing else in this module moved.
from ..platform.windows import _clamp, _norm1000_to_pixels, _to_pixels  # noqa: F401,E402  # Phase 3 D4d: cursor hub moved to core/desktop/; platform stays flat in core/


def _clamp_to_screen(x: int, y: int, size):
    if size is None:
        return x, y
    w, h = size
    return _clamp(x, 0, w - 1), _clamp(y, 0, h - 1)


def _virtual_bounds():
    """(min_x, min_y, max_x, max_y) inclusive bounds of the VIRTUAL
    screen — every monitor stitched together — or None if unknown.

    Windows reports the virtual desktop via GetSystemMetrics
    (SM_XVIRTUALSCREEN 76, SM_YVIRTUALSCREEN 77, SM_CXVIRTUALSCREEN
    78, SM_CYVIRTUALSCREEN 79): secondary monitors can sit at
    NEGATIVE coordinates, which a naive 0..width-1 clamp would fence
    out entirely. Elsewhere (and on any failure) falls back to the
    primary screen from _screen_size(). Never raises."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        _bounds = _backend.virtual_bounds()
        if _bounds is not None:
            return _bounds
    if _is_windows():
        try:
            import ctypes

            user32 = ctypes.windll.user32
            left = int(user32.GetSystemMetrics(76))
            top = int(user32.GetSystemMetrics(77))
            width = int(user32.GetSystemMetrics(78))
            height = int(user32.GetSystemMetrics(79))
            if width > 0 and height > 0:
                return left, top, left + width - 1, top + height - 1
        except Exception:  # noqa: BLE001
            pass
    size = _screen_size()
    if size is None:
        return None
    w, h = size
    return 0, 0, w - 1, h - 1


def _norm1000_reference():
    """(origin_x, origin_y, width, height) the norm1000 grid maps
    across — the coordinate contract (desktop programme, F-01/F-02).

    The model reads coordinates off the LAST CAPTURED IMAGE, so
    the grid is that image's rectangle on the screen: a cropped
    foreground-window capture maps across its crop rectangle
    (crop_left/crop_top/crop_width/crop_height from
    vision.last_capture_info()); an uncropped capture maps across
    the frame it grabbed (its recorded screen size, with the
    virtual desktop's origin when the frame IS the virtual
    desktop, so a secondary monitor at negative coordinates still
    maps right). With no capture on record the grid is the
    virtual desktop bounds — every monitor stitched together —
    never the primary screen alone. None when nothing can be
    measured. Never raises: a missing or malformed record falls
    through to the next source, and a caller that gets None
    reports the conversion as impossible instead of dispatching
    against a guessed grid."""
    try:
        from . import vision as _vision  # Phase 3 D4d: cursor hub now in core/desktop/; D4c interim closed

        info = _vision.last_capture_info()
    except Exception:  # noqa: BLE001 — no record, fall through
        info = None
    if info:
        try:
            if info.get("cropped"):
                cl = int(info["crop_left"])
                ct = int(info["crop_top"])
                cw = int(info["crop_width"])
                ch = int(info["crop_height"])
                if cw > 0 and ch > 0:
                    return cl, ct, cw, ch
            else:
                sw = int(info["screen_width"])
                sh = int(info["screen_height"])
                if sw > 0 and sh > 0:
                    bounds = _virtual_bounds()
                    if bounds is not None:
                        min_x, min_y, max_x, max_y = bounds
                        if (max_x - min_x + 1 == sw
                                and max_y - min_y + 1 == sh):
                            return min_x, min_y, sw, sh
                    return 0, 0, sw, sh
        except (KeyError, TypeError, ValueError):
            pass
    bounds = _virtual_bounds()
    if bounds is None:
        return None
    min_x, min_y, max_x, max_y = bounds
    return min_x, min_y, max_x - min_x + 1, max_y - min_y + 1


def _norm1000_to_screen(nx, ny):
    """Absolute screen pixels for a norm1000 point under the
    coordinate contract, or None when the reference grid cannot
    be measured. Raises TypeError/ValueError for non-numeric
    input, exactly as _norm1000_to_pixels does — callers map that
    to their own 'must be numbers' message. The (dim - 1) scaling
    is _norm1000_to_pixels' own, so norm1000 (500, 500) lands
    exactly where move_fraction (0.5, 0.5) of the same rectangle
    does."""
    ref = _norm1000_reference()
    if ref is None:
        return None
    ox, oy, w, h = ref
    px, py = _norm1000_to_pixels(nx, ny, w, h)
    return ox + px, oy + py


def _capture_freshness_verdict():
    """(code, message) or (None, None) — the norm1000 capture-freshness
    check with its failure code (control-existence C9, 2026-11-05).
    The message strings are identical to _capture_freshness_refusal's;
    that wrapper below preserves the old str|None shape for the
    cursor_control hook and its tests.

    WHY: a norm1000 point carries no provenance — no timestamp, no
    snapshot id, no target identity — and the conversion grid is
    whatever the LAST capture recorded, read at ACTION time
    (_norm1000_reference). So a point read off one screenshot can be
    mapped through a different screenshot's grid, or onto a window
    that has since moved, and the old path executed it blindly
    (_fence_point clamps a stale target into bounds instead of
    refusing it). This check refuses on POSITIVE evidence that the
    world moved since the last capture, using only seams the tree
    already has: the last capture's crop record, the current
    foreground window rectangle, and the current virtual bounds.

    Cases refused (each names what changed + the re-observe
    instruction, never a silent wrong-target action):
    * the virtual desktop's bounds changed since the capture
      (resolution / monitor change) — the grid itself moved. The
      comparison is live-vs-recorded through the SAME probe
      (vision._virtual_screen_bounds), never against the recorded
      screen size: on a multi-monitor desktop the virtual width
      legitimately differs from the recorded screen size, and that
      difference is not evidence of change (this is what the
      F-01 crop-wins test pins);
    * the last capture was a foreground-window crop and the current
      foreground window's rectangle differs from the recorded crop
      (window moved, resized, closed, or another window came
      forward) — the crop grid no longer covers what was seen.
    * the last capture was a foreground-window crop and the
      foreground window's IDENTITY differs from the recorded one
      (same rectangle, different window — C8): hwnd first, title
      fallback when either handle is unknown (the F-07 rule).

    Deliberately NOT refused (pass-through, mirroring the tree's
    guarded posture — a missing or malformed record falls through,
    and validation that cannot run is not a refusal):
    * no capture on record (the grid falls back to virtual bounds,
      as before — coordinates with no capture behind them are the
      caller's problem, unchanged);
    * an unreadable current bounds or foreground rectangle
      (headless / lab — the check cannot run, so it does not invent
      a refusal);
    * a monitor crop (DISPLAY-01: the crop is a caller-supplied
      monitor rectangle, not a window — the foreground comparison
      does not apply; the bounds check still does).

    ABSENCE OF A REFUSAL IS NOT A FRESHNESS CERTIFICATE. What this
    check cannot see, stated plainly: in-window content change
    (scroll, re-sort, a dialog swapped inside the same window at
    the same rectangle); a same-rectangle window swap when
    the record carries no window identity to compare (C8,
    2026-11-05: when the record
    DOES carry foreground identity, the identity branch below
    refuses the swap; unknown identity still skips, never
    refuses); and an intervening capture with a
    different crop, when the foreground has since settled on the
    newer capture's window (the check validates against the LAST
    capture, which is then the wrong baseline for coordinates read
    off the older image). The durable answer for a target where a
    wrong click is costly is identity-based targeting
    (click_element / uia_click_control / click_text / marks /
    handles — the re-proof paths), which this check does not
    replace; it only stops the blind geometry cases.
    """
    try:
        from . import vision as _vision
        info = _vision.last_capture_info()
    except Exception:  # noqa: BLE001 — no record, fall through
        return None, None
    if not isinstance(info, dict):
        return None, None
    try:
        from .target_handles import REOBSERVE_INSTRUCTION
    except Exception:  # noqa: BLE001 — wording fallback, same meaning
        REOBSERVE_INSTRUCTION = (
            "Re-observe (take_screenshot or screen_look) and pick a "
            "fresh target.")
    # Virtual-bounds comparison, same probe both sides: the record
    # carries the bounds measured at capture time (absent on old /
    # hand-built records, which skip this branch, not refuse).
    recorded_vb = info.get("virtual_bounds")
    if recorded_vb is not None:
        try:
            live_vb = _vision._virtual_screen_bounds()
        except Exception:  # noqa: BLE001 — unreadable, fall through
            live_vb = None
        if live_vb is not None:
            # Both sides must be 4-int tuples, else the branch
            # skips: a malformed bounds value never refuses.
            try:
                rec = tuple(int(v) for v in recorded_vb)
                live = tuple(int(v) for v in live_vb)
            except (TypeError, ValueError):
                rec = live = None
            if (rec is not None and len(rec) == 4
                    and len(live) == 4 and rec != live):
                return ("bounds-changed",
                    f"The screen arrangement changed since the "
                    f"last screenshot (virtual bounds were "
                    f"{rec}, now {live}), so these norm1000 "
                    f"coordinates no longer map to the same "
                    f"points. {REOBSERVE_INSTRUCTION}")
    if not info.get("cropped") or info.get("monitor_crop"):
        return None, None
    # Control-existence C8 (2026-11-05): same-rectangle window
    # swap — two maximised windows share one rect, so the rect
    # comparison below passes a swap. When the record carries
    # foreground identity, compare it (hwnd first, title
    # fallback when either handle is unknown — the F-07 rule).
    # Unknown identity on either side skips, never refuses.
    rec_hwnd = info.get("foreground_hwnd")
    rec_title = info.get("foreground_title")
    if rec_hwnd is not None or rec_title:
        live_hwnd, live_title = _live_foreground_identity()
        if (rec_hwnd is not None and live_hwnd is not None
                and str(rec_hwnd) != str(live_hwnd)):
            return (
                "stale-foreground",
                f"The foreground window changed since the "
                f"last screenshot (a different window is now "
                f"in front at the same rectangle), so these "
                f"norm1000 coordinates no longer point at "
                f"what was seen. {REOBSERVE_INSTRUCTION}")
        if ((rec_hwnd is None or live_hwnd is None)
                and rec_title and live_title
                and str(rec_title) != str(live_title)):
            return (
                "stale-foreground",
                f"The foreground window changed since the "
                f"last screenshot (its title was "
                f"'{rec_title}', now '{live_title}'), so "
                f"these norm1000 coordinates no longer point "
                f"at what was seen. {REOBSERVE_INSTRUCTION}")
    try:
        cl = int(info["crop_left"])
        ct = int(info["crop_top"])
        cw = int(info["crop_width"])
        ch = int(info["crop_height"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if cw <= 0 or ch <= 0:
        return None, None
    try:
        rect = _vision.foreground_window_rect()
    except Exception:  # noqa: BLE001 — unreadable, fall through
        return None, None
    if rect is None:
        return None, None
    try:
        rl, rt, rr, rb = (int(v) for v in rect)
    except (TypeError, ValueError):
        return None, None
    if (rl, rt, rr, rb) != (cl, ct, cl + cw, ct + ch):
        return ("stale-foreground",
            f"The foreground window moved, resized, or changed since "
            f"the last screenshot (its rectangle was "
            f"({cl}, {ct}, {cl + cw}, {ct + ch}), now "
            f"({rl}, {rt}, {rr}, {rb})), so these norm1000 "
            f"coordinates no longer point at what was seen. "
            f"{REOBSERVE_INSTRUCTION}")
    return None, None




def _live_foreground_identity():
    """(hwnd, title) of the live foreground window, each None
    when unreadable. Guarded: identity that cannot be read
    never counts as a change."""
    hwnd = None
    title = None
    try:
        from ..skills_window_control import _foreground_hwnd
        hwnd = _foreground_hwnd()
    except Exception:  # noqa: BLE001 — unknown, not a refusal
        hwnd = None
    try:
        from ..vision_exclusions import foreground_probe
        _proc, _title = foreground_probe()
        title = _title or None
    except Exception:  # noqa: BLE001 — unknown, not a refusal
        title = None
    return hwnd, title


def _capture_freshness_refusal():
    """Refusal string or None — the pre-C9 shape, preserved for
    the cursor_control hook and its tests. New callers that
    need the failure code use _capture_freshness_verdict."""
    _code, message = _capture_freshness_verdict()
    return message

def _fence_point(x, y, bounds=None):
    """Clamp (x, y) into the virtual-screen bounds — the bounds fence
    every dispatched move/click passes through. Out-of-range points
    land on the nearest in-bounds point, so a stale or hallucinated
    target still completes as a move instead of erroring at the OS.
    Unknown bounds → the point passes through unchanged (we cannot
    fence what we cannot measure). Non-numeric input passes through
    untouched for the caller's own validation to report."""
    _backend = _routed_backend()  # v5.50 (items 41/42)
    if _backend is not None:
        if bounds is not None:
            return _backend.fence_point(x, y, bounds=bounds)
        # Reconciliation (v5.50 items 41/42): the routed branch
        # originally delegated with bounds=None, so the serving
        # backend resolved the bounds from ITS OWN virtual_bounds
        # and this module's _virtual_bounds seam was silently
        # dropped — the legacy body below resolves implicit bounds
        # through the module-global _virtual_bounds() lookup, and
        # core/platform/windows.py documents that lookup as a
        # preserved seam ("cursor._fence_point reads
        # cursor._virtual_bounds through its own module
        # namespace"). The shipped fencing suite
        # (tests/test_cursor_fencing.py) fakes geometry exactly
        # there. Honour the seam on ANY serving backend: resolve
        # implicit bounds through _virtual_bounds() here (when
        # unpatched, that function itself routes to the backend
        # first and then falls back to _screen_size(), so
        # production behaviour is the backend's bounds or that
        # documented fallback), then delegate with explicit
        # bounds. Unknown bounds pass the (converted) point
        # through, exactly as the legacy body does; non-numeric
        # input passes through untouched. The Windows-serving
        # path is untouched: _routed_backend() returns None when
        # the Windows backend serves, so the legacy body below
        # still runs unchanged there.
        try:
            xi, yi = int(round(float(x))), int(round(float(y)))
        except (TypeError, ValueError):
            return x, y
        bounds = _virtual_bounds()
        if bounds is None:
            return xi, yi
        return _backend.fence_point(xi, yi, bounds=bounds)
    try:
        xi, yi = int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        return x, y
    if bounds is None:
        bounds = _virtual_bounds()
    if bounds is None:
        return xi, yi
    min_x, min_y, max_x, max_y = bounds
    return _clamp(xi, min_x, max_x), _clamp(yi, min_y, max_y)
