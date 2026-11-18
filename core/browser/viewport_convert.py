"""Viewport -> screen conversion (click-anywhere Plan 9, section 1.1).

own code - nothing adopted (Parent-ruled (licence, 2026-11-14))

The corrected conversion for a browser window that may straddle monitors
with different DPIs. The client rect is split per monitor (R-1, R-2);
the left-border check sums over ONE ROW (the primary slice's row, T-2)
and the top-chrome derivation sums over ONE COLUMN (the first slice's
column, T-1). Every worked number in Plan 9 section 1.1 is reproduced
by the lab tests in ``tests/test_click_anywhere_b1_conversion.py``.

Return shape (one shape, used consistently)
--------------------------------------------
Every public function that can refuse returns a dict. On success the
dict carries ``"refused": False`` plus the result fields. On refusal
it carries ``{"refused": True, "reason": <str>, "rule": <str>}`` and
nothing dispatches on it. The legacy discrimination helpers are the
sole exception: they return plain numbers (the value the superseded
rule would have produced), because their job is to show the old rule
failing, not to convert.

Refusal strings are the plan's wording where the plan quotes it:
"monitor split does not tile - re-observe",
"monitor scale unknown - cannot convert on this monitor",
"chrome straddles monitors - move the window onto one monitor",
"scrollbar straddles monitors - move the window onto one monitor",
"monitor ambiguous - re-observe",
"observation superseded - re-observe".

Coordinate conventions
----------------------
All rects are physical pixels, half-open: ``(left, top, right, bottom)``
with ``width == right - left``. Monitor rects come from the tree's
existing ``read_table(backend=None)`` (``core/desktop/monitors.py:657``)
and the split uses the rcMonitor fields (``left``/``top``/``width``/
``height``) ONLY - the ``work_*`` fields are never read (U-4). A
monitor whose ``scale_x``/``scale_y`` are None refuses any conversion
that needs its scale, and never silently takes another monitor's scale
or S=1.

Win32 calls live behind ``sys.platform == "win32"`` guards and behind
injectable provider seams (the ``core/desktop/uia_engine.py:83``
``set_engine_override`` pattern): the monitor table, the DPI readers,
the window-rect source and the MonitorFromPoint source all have
module-level override setters, so the Linux lab tests can inject fakes.
Importing this module on Linux performs no Win32 call.
"""

import sys

from core.desktop.monitors import read_table as _tree_read_table

__all__ = [
    "check_devtools_delta",
    "check_dpi_awareness",
    "check_pass_id",
    "convert_viewport_to_screen",
    "derive_top_chrome",
    "is_refusal",
    "left_border_check",
    "legacy_all_slices_check",
    "legacy_all_slices_derivation",
    "legacy_page_dpr_scrollbar_derivation",
    "legacy_plan4_assembly",
    "legacy_plan6_derivation",
    "monitor_at_point",
    "reset_all_overrides",
    "reset_dpi_reader_override",
    "reset_monitor_from_point_override",
    "reset_monitor_table_override",
    "reset_window_rect_override",
    "set_dpi_reader_override",
    "set_monitor_from_point_override",
    "set_monitor_table_override",
    "set_window_rect_override",
    "split_client_rect",
]

# ---------------------------------------------------------------------------
# Refusal shape
# ---------------------------------------------------------------------------

REASON_TILING = "monitor split does not tile - re-observe"
REASON_SCALE_UNKNOWN = "monitor scale unknown - cannot convert on this monitor"
REASON_CHROME_SPILL = "chrome straddles monitors - move the window onto one monitor"
REASON_SCROLLBAR_SPILL = (
    "scrollbar straddles monitors - move the window onto one monitor"
)
REASON_AMBIGUOUS = "monitor ambiguous - re-observe"
REASON_SUPERSEDED = "observation superseded - re-observe"
REASON_BOUNDARY_CROSS = "element rect crosses a monitor boundary - re-observe"

#: DPI-awareness levels the conversion accepts (P-10, Q-10).
DPI_AWARE_LEVELS = ("per-monitor-v1", "per-monitor-v2")


def _refusal(reason, rule):
    return {"refused": True, "reason": reason, "rule": rule}


def is_refusal(result):
    """True when a returned dict is a refusal (``refused: True``)."""
    return isinstance(result, dict) and result.get("refused") is True


# ---------------------------------------------------------------------------
# Provider seams (test-only overrides, uia_engine.set_engine_override pattern)
# ---------------------------------------------------------------------------

_MONITOR_TABLE_OVERRIDE = None
_DPI_READER_OVERRIDE = None
_WINDOW_RECT_OVERRIDE = None
_MONITOR_FROM_POINT_OVERRIDE = None


def set_monitor_table_override(table_or_callable):
    """Inject a fake monitor table (a list) or a fake table reader
    (a callable returning a list) for the split. Tests only."""
    global _MONITOR_TABLE_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _MONITOR_TABLE_OVERRIDE = table_or_callable


def reset_monitor_table_override():
    """Restore the live ``read_table`` source."""
    global _MONITOR_TABLE_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _MONITOR_TABLE_OVERRIDE = None


def set_dpi_reader_override(fn):
    """Inject a fake DPI reader: ``fn(monitor_dict) -> scale`` (float)
    or None when the monitor's DPI is unreadable. Tests only. The
    production reader is ``GetDpiForMonitor`` via the tree's monitor
    table (scale_x/scale_y); this seam replaces it, it does not add a
    second live enumeration."""
    global _DPI_READER_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _DPI_READER_OVERRIDE = fn


def reset_dpi_reader_override():
    """Restore the table-based DPI reader."""
    global _DPI_READER_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _DPI_READER_OVERRIDE = None


def set_window_rect_override(fn):
    """Inject a fake window-rect source: ``fn(hwnd) -> (l, t, r, b)``
    client rect in physical px, or None. Tests only. The production
    source is Win32 ``GetClientRect`` + ``ClientToScreen`` (win32
    only, behind the platform guard)."""
    global _WINDOW_RECT_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _WINDOW_RECT_OVERRIDE = fn


def reset_window_rect_override():
    """Restore the production window-rect source."""
    global _WINDOW_RECT_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _WINDOW_RECT_OVERRIDE = None


def set_monitor_from_point_override(fn):
    """Inject a fake MonitorFromPoint: ``fn(x, y) -> monitor_dict``
    or None. Tests only. The production source is Win32
    ``MonitorFromPoint`` (win32 only, behind the platform guard)."""
    global _MONITOR_FROM_POINT_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _MONITOR_FROM_POINT_OVERRIDE = fn


def reset_monitor_from_point_override():
    """Restore the production MonitorFromPoint source."""
    global _MONITOR_FROM_POINT_OVERRIDE  # noqa: PLW0603 - the seam is the point
    _MONITOR_FROM_POINT_OVERRIDE = None


def reset_all_overrides():
    """Restore every provider seam (suite/prover tearDown)."""
    reset_monitor_table_override()
    reset_dpi_reader_override()
    reset_window_rect_override()
    reset_monitor_from_point_override()


# ---------------------------------------------------------------------------
# Monitor table + scale reads
# ---------------------------------------------------------------------------

def get_monitor_table():
    """The current monitor table: the override when set, else the
    tree's ``read_table(backend=None)``. Returns ``(table, note)``;
    ``note`` is "" on success."""
    if _MONITOR_TABLE_OVERRIDE is not None:
        override = _MONITOR_TABLE_OVERRIDE
        try:
            table = override() if callable(override) else override
        except Exception as exc:  # noqa: BLE001 - a broken fake
            return [], f"Could not read the monitor list: {exc}."
        return list(table or []), ""
    return _tree_read_table(backend=None)


def _monitor_scale(monitor):
    """The monitor's display scale S (unitless), or None when its DPI
    cannot be read. ``scale_x`` fills in for ``scale_y`` and vice
    versa; a non-positive value is unreadable, never a scale."""
    if _DPI_READER_OVERRIDE is not None:
        try:
            value = _DPI_READER_OVERRIDE(monitor)
        except Exception:  # noqa: BLE001 - a broken fake reads as None
            return None
        return _clean_scale(value)
    sx = monitor.get("scale_x", monitor.get("scale"))
    sy = monitor.get("scale_y", monitor.get("scale"))
    for value in (sx, sy):
        cleaned = _clean_scale(value)
        if cleaned is not None:
            return cleaned
    return None


def _clean_scale(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _monitor_id(monitor):
    if isinstance(monitor, dict):
        return str(monitor.get("monitor_id") or monitor.get("device") or "")
    return ""


def _monitor_rect(monitor):
    """The monitor's rcMonitor rect ``(left, top, right, bottom)``.

    U-4: built from ``left``/``top``/``width``/``height`` ONLY. The
    ``work_*`` fields are never read anywhere in this module - a
    monitor whose work rect disagrees with its monitor rect changes
    nothing here (proved by the lab test that poisons them). Floats
    are kept throughout: a synthetic 643.75 px monitor edge must
    not truncate to 643."""
    left = float(monitor["left"])
    top = float(monitor["top"])
    return (
        left,
        top,
        left + float(monitor["width"]),
        top + float(monitor["height"]),
    )


def virtual_bounds(table):
    """``(min_x, min_y, max_x, max_y)`` inclusive union of the table
    (rcMonitor rects only), or None for an empty table."""
    if not table:
        return None
    try:
        rects = [_monitor_rect(m) for m in table]
    except (KeyError, TypeError, ValueError):
        return None
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[2] for r in rects) - 1,
        max(r[3] for r in rects) - 1,
    )


def monitor_at_point(x, y, table=None):
    """The monitor dict containing point ``(x, y)`` in physical px,
    or None. The injected MonitorFromPoint provider wins when set;
    otherwise the table's rcMonitor rects decide (half-open, so a
    point on a shared edge belongs to the monitor to its right /
    below)."""
    if _MONITOR_FROM_POINT_OVERRIDE is not None:
        try:
            return _MONITOR_FROM_POINT_OVERRIDE(float(x), float(y))
        except Exception:  # noqa: BLE001 - a broken fake reads as None
            return None
    if table is None:
        table, _note = get_monitor_table()
    for monitor in table or []:
        try:
            left, top, right, bottom = _monitor_rect(monitor)
        except (KeyError, TypeError, ValueError):
            continue
        if left <= x < right and top <= y < bottom:
            return monitor
    return None


def _window_client_rect(hwnd):
    """The window's client rect ``(l, t, r, b)`` in physical px, or
    None. Win32 only: ``GetClientRect`` + ``ClientToScreen``; the
    injected window-rect source wins when set (lab fake)."""
    if _WINDOW_RECT_OVERRIDE is not None:
        try:
            return _WINDOW_RECT_OVERRIDE(hwnd)
        except Exception:  # noqa: BLE001 - a broken fake reads as None
            return None
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    rect = RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    origin = POINT(rect.left, rect.top)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return (
        int(origin.x),
        int(origin.y),
        int(origin.x) + int(rect.right) - int(rect.left),
        int(origin.y) + int(rect.bottom) - int(rect.top),
    )


def _window_scale(hwnd):
    """The window's S from Win32 ``GetDpiForWindow`` (dpi / 96), or
    None off Windows / on failure. The injected DPI reader wins when
    set (lab fake, keyed on the hwnd's monitor dict)."""
    if _DPI_READER_OVERRIDE is not None:
        return None  # the reader is keyed on monitor dicts, not hwnds
    if sys.platform != "win32":
        return None
    import ctypes

    user32 = ctypes.windll.user32
    try:
        dpi = user32.GetDpiForWindow(hwnd)
    except Exception:  # noqa: BLE001 - API absent / call failed
        return None
    return _clean_scale(dpi / 96.0) if dpi else None


# ---------------------------------------------------------------------------
# Guards: pass id (R-10), DevTools delta (N-2), DPI awareness (P-10, Q-10)
# ---------------------------------------------------------------------------

def check_pass_id(observation_pass_id, latest_pass_id):
    """The pass-id protocol (R-10): convert only when the observation
    handed over is still the latest. Returns ``{"ok": True}`` or the
    refusal ``"observation superseded - re-observe"``. Either id
    being None means no latest is known, so there is nothing to
    supersede."""
    if observation_pass_id is None or latest_pass_id is None:
        return {"ok": True}
    if observation_pass_id == latest_pass_id:
        return {"ok": True}
    return _refusal(REASON_SUPERSEDED, "R-10")


def _delta_pair(delta):
    """A DevTools delta as ``(height_delta, width_delta)`` floats,
    accepting a ``(h, w)`` tuple/list or a ``{"height": h,
    "width": w}`` dict. Returns None when unreadable."""
    try:
        if isinstance(delta, dict):
            return (float(delta["height"]), float(delta["width"]))
        return (float(delta[0]), float(delta[1]))
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def check_devtools_delta(baseline, current):
    """The DevTools session baseline (N-2): the
    ``(outerHeight - innerHeight, outerWidth - innerWidth)`` deltas
    recorded at session start, compared against the current deltas.
    A mid-goal change refuses and re-baselines. The delta is a
    change-detector only, never a measurement. Returns
    ``{"ok": True}`` or a refusal. Either side missing means there
    is no baseline to compare, so there is nothing to refuse on."""
    if baseline is None or current is None:
        return {"ok": True}
    base = _delta_pair(baseline)
    now = _delta_pair(current)
    if base is None or now is None:
        return {"ok": True}
    if base == now:
        return {"ok": True}
    return _refusal(
        "DevTools delta changed mid-goal "
        f"(baseline {base}, now {now}) - re-baseline",
        "N-2",
    )


def check_dpi_awareness(level):
    """The DPI-awareness assertion (P-10, Q-10): the conversion
    asserts the host's reported level is per-monitor-aware (v1 or
    v2). System / unaware / unknown / missing refuses. Returns
    ``{"ok": True}`` or a refusal."""
    if level in DPI_AWARE_LEVELS:
        return {"ok": True}
    return _refusal(
        f"DPI awareness level {level!r} is not per-monitor-aware - "
        "cannot convert",
        "P-10",
    )


# ---------------------------------------------------------------------------
# Per-monitor split (R-1, R-2)
# ---------------------------------------------------------------------------

def split_client_rect(client_rect, table=None):
    """Split the client rect (physical px) by monitor. Returns a list
    of slice dicts, one per monitor the rect overlaps::

        {"monitor": entry, "monitor_id": str, "primary": bool,
         "left": .., "top": .., "right": .., "bottom": ..,
         "width": .., "height": .., "scale": S or None}

    A zero-width / zero-height intersection contributes nothing.
    A single-monitor window yields exactly one slice, so every rule
    below reduces exactly to Plan 6's rule. The split uses rcMonitor
    fields only (U-4)."""
    if table is None:
        table, _note = get_monitor_table()
    try:
        cl, ct, cr, cb = (float(v) for v in client_rect)
    except (TypeError, ValueError):
        return []
    slices = []
    for monitor in table or []:
        try:
            ml, mt, mr, mb = _monitor_rect(monitor)
        except (KeyError, TypeError, ValueError):
            continue
        left = max(cl, ml)
        top = max(ct, mt)
        right = min(cr, mr)
        bottom = min(cb, mb)
        if right <= left or bottom <= top:
            continue
        slices.append({
            "monitor": monitor,
            "monitor_id": _monitor_id(monitor),
            "primary": bool(monitor.get("primary")),
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "width": right - left,
            "height": bottom - top,
            "scale": _monitor_scale(monitor),
        })
    return slices


def _slice_at_point(slices, x, y):
    """The slice containing point ``(x, y)`` (half-open), or None."""
    for sl in slices:
        if sl["left"] <= x < sl["right"] and sl["top"] <= y < sl["bottom"]:
            return sl
    return None


def _require_scale(slice_):
    """The slice's S, or the None-scale refusal dict when its DPI
    cannot be read. A None-scale monitor never takes another
    monitor's scale, and never takes S=1."""
    scale = slice_["scale"]
    if scale is None:
        return None, _refusal(REASON_SCALE_UNKNOWN, "T-6")
    return scale, None


def _tiles(slices, start, end, key):
    """True when ``slices`` (ordered by ``key``) start at ``start``,
    end at ``end`` and abut (each slice's far edge is the next
    slice's near edge)."""
    if not slices:
        return False
    ordered = sorted(slices, key=lambda s: s[key])
    if abs(ordered[0][key] - start) > 1e-6:
        return False
    far = {"left": "right", "top": "bottom"}[key]
    if abs(ordered[-1][far] - end) > 1e-6:
        return False
    for prev, nxt in zip(ordered, ordered[1:]):
        if abs(prev[far] - nxt[key]) > 1e-6:
            return False
    return True


# ---------------------------------------------------------------------------
# Left-border check, straddle-aware (R-1, T-2)
# ---------------------------------------------------------------------------

def left_border_check(slices, client_rect, viewport_width_css,
                      scrollbar_width_css, z, primary_monitor_id=None):
    """The straddle-aware left-border check (R-1):

    ``sum over ONE ROW B of (w_m / DPR_m) - viewport_width_CSS
    - scrollbar_width_CSS <= 2``

    B is the row containing the PRIMARY slice - the slices sharing
    the primary slice's top edge. The primary slice is the slice on
    the window's primary monitor (``primary_monitor_id``, else the
    table's primary monitor). A None-scale monitor in B refuses.
    The tiling precondition (B must start at the client left, end at
    the client right, abut) refuses with
    "monitor split does not tile - re-observe". The check is
    one-sided: it guards against the client being WIDER than
    expected; a narrower reading passes.

    Returns ``{"refused": False, "value": float, "row": [slices]}``
    or a refusal dict (tiling, None-scale, or the value exceeding
    the 2 CSS px limit). A negative value passes: the check is
    one-sided by design."""
    try:
        cl, _ct, cr, _cb = (float(v) for v in client_rect)
        z = float(z)
        viewport = float(viewport_width_css)
        scrollbar = float(scrollbar_width_css)
    except (TypeError, ValueError):
        return _refusal("left-border check needs numbers - re-observe", "R-1")
    if z <= 0:
        return _refusal("page zoom must be positive - re-observe", "R-1")
    primary = None
    if primary_monitor_id is not None:
        wanted = str(primary_monitor_id)
        primary = next(
            (s for s in slices if s["monitor_id"] == wanted), None)
    if primary is None:
        primary = next((s for s in slices if s["primary"]), None)
    if primary is None:
        primary = slices[0] if slices else None
    if primary is None:
        return _refusal("no monitor slice to check - re-observe", "R-1")
    row = [s for s in slices if s["top"] == primary["top"]]
    if not _tiles(row, cl, cr, "left"):
        return _refusal(REASON_TILING, "T-2")
    total = 0.0
    for sl in row:
        scale, refusal = _require_scale(sl)
        if refusal is not None:
            return refusal
        total += sl["width"] / (scale * z)
    value = total - viewport - scrollbar
    if value > 2:
        return _refusal(
            f"left-border check failed: client is {value:.2f} CSS px "
            "wider than the viewport + scrollbar (limit 2) - re-observe",
            "R-1",
        )
    return {"refused": False, "value": value, "row": row}


def legacy_all_slices_check(slices, viewport_width_css,
                            scrollbar_width_css, z):
    """Plan 8's all-slices sum (T-2, discrimination only): the same
    sum over ALL slices, not one row. Returns the float value. On a
    vertical straddle it double-counts the width (845.83 / 1218.0 /
    1015.00 on the plan's fixtures) and refuses a legitimate window -
    the lab test asserts it fails where the one-row check passes.
    A None-scale slice has no DPR; it is skipped here because this
    helper only ever runs on the plan's fully-scaled fixtures."""
    total = 0.0
    for sl in slices:
        scale = sl["scale"]
        if scale is None:
            continue
        total += sl["width"] / (scale * float(z))
    return total - float(viewport_width_css) - float(scrollbar_width_css)


# ---------------------------------------------------------------------------
# Top-chrome derivation, per monitor slice (R-2, T-1, S-2a)
# ---------------------------------------------------------------------------

def derive_top_chrome(slices, client_rect, viewport_height_css,
                      inner_height_css, z, first_slice=None,
                      last_slice=None):
    """The per-monitor-slice top-chrome derivation (R-2):

    ``chrome_phys = h_first
        - DPR_first * (viewport_height_CSS
                       - sum over C, m != first of viewport_phys_m / DPR_m)
        - (h_scrollbar_physical if first == last else 0)``
    ``top_chrome_CSS = chrome_phys / S_first``

    C is the FIRST slice's COLUMN: the slices sharing the first
    slice's left edge, below it. The first slice is the slice
    containing the window's TOP-LEFT corner; the last slice is the
    slice containing the window's BOTTOM-LEFT corner (both may be
    passed explicitly - the wrong-column lab case anchors the
    derivation at the right column on purpose). ``viewport_phys_m``
    is ``h_m`` for a middle slice and ``h_last -
    h_scrollbar_physical`` for the last slice (when first != last);
    ``h_scrollbar_physical = (innerHeight - clientHeight) *
    DPR_last`` - the LAST slice's DPR, not the page DPR (S-2a).

    The column tiling precondition (C must start at the first
    slice's bottom, end at the client bottom, abut) refuses with
    "monitor split does not tile - re-observe". The spill guards
    refuse: ``0 < chrome_phys <= h_first`` else
    "chrome straddles monitors - move the window onto one monitor";
    ``h_scrollbar_physical <= h_last`` else
    "scrollbar straddles monitors - move the window onto one monitor".

    Returns ``{"refused": False, "chrome_phys": float,
    "top_chrome_css": float, "h_scrollbar_physical": float,
    "column": [slices]}`` or a refusal dict."""
    try:
        _cl, _ct, _cr, cb = (float(v) for v in client_rect)
        z = float(z)
        viewport = float(viewport_height_css)
        inner_height = float(inner_height_css)
    except (TypeError, ValueError):
        return _refusal("top-chrome derivation needs numbers - re-observe", "R-2")
    if z <= 0:
        return _refusal("page zoom must be positive - re-observe", "R-2")
    if not slices:
        return _refusal("no monitor slice to derive from - re-observe", "R-2")
    if first_slice is None:
        first_slice = _slice_at_point(slices, client_rect[0], client_rect[1])
    if last_slice is None:
        last_slice = _slice_at_point(
            slices, client_rect[0], client_rect[3] - 1)
    if first_slice is None or last_slice is None:
        return _refusal("no monitor slice to derive from - re-observe", "R-2")
    column = [s for s in slices if s["left"] == first_slice["left"]]
    column.sort(key=lambda s: s["top"])
    if column[0] is not first_slice:
        return _refusal(REASON_TILING, "T-1")
    if not _tiles(column, first_slice["top"], cb, "top"):
        return _refusal(REASON_TILING, "T-1")
    s_first, refusal = _require_scale(first_slice)
    if refusal is not None:
        return refusal
    s_last, refusal = _require_scale(last_slice)
    if refusal is not None:
        return refusal
    dpr_first = s_first * z
    dpr_last = s_last * z
    # Horizontal scrollbar sits on the LAST slice: its physical
    # height scales by the LAST slice's DPR, not the page DPR (S-2a).
    h_scrollbar = max(0.0, inner_height - viewport) * dpr_last
    if h_scrollbar > last_slice["height"]:
        return _refusal(REASON_SCROLLBAR_SPILL, "R-2")
    total = 0.0
    for sl in column:
        if sl is first_slice:
            continue
        scale, refusal = _require_scale(sl)
        if refusal is not None:
            return refusal
        if sl is last_slice:
            viewport_phys = sl["height"] - h_scrollbar
        else:
            viewport_phys = sl["height"]
        total += viewport_phys / (scale * z)
    chrome_phys = (
        first_slice["height"]
        - dpr_first * (viewport - total)
        - (h_scrollbar if first_slice is last_slice else 0.0)
    )
    if not (0.0 < chrome_phys <= first_slice["height"]):
        return _refusal(REASON_CHROME_SPILL, "R-2")
    return {
        "refused": False,
        "chrome_phys": chrome_phys,
        "top_chrome_css": chrome_phys / s_first,
        "h_scrollbar_physical": h_scrollbar,
        "column": column,
    }


def legacy_all_slices_derivation(slices, client_rect,
                                 viewport_height_css, z, first_slice,
                                 h_scrollbar_physical=0.0):
    """Plan 8's all-slices sum (T-1, discrimination only): the same
    derivation summing over ALL non-first slices, not the first
    slice's column. Returns ``chrome_phys`` as a float. On a
    horizontal straddle it gives 1016.67 (top_chrome 813.33 vs true
    80); on a 2x2 grid 1200 (a false refusal). The lab test asserts
    it fails where the column-sum derivation passes."""
    s_first = first_slice["scale"]
    dpr_first = s_first * float(z)
    last = _slice_at_point(slices, client_rect[0], client_rect[3] - 1)
    total = 0.0
    for sl in slices:
        if sl is first_slice:
            continue
        scale = sl["scale"]
        if scale is None:
            continue
        viewport_phys = sl["height"]
        if last is not None and sl is last and first_slice is not last:
            viewport_phys = sl["height"] - h_scrollbar_physical
        total += viewport_phys / (scale * float(z))
    return (
        first_slice["height"]
        - dpr_first * (float(viewport_height_css) - total)
        - (h_scrollbar_physical if first_slice is last else 0.0)
    )


def legacy_page_dpr_scrollbar_derivation(slices, client_rect,
                                         viewport_height_css,
                                         inner_height_css, z,
                                         dpr_page, first_slice=None,
                                         last_slice=None):
    """The S-2a discrimination helper: the slice derivation with the
    OLD scrollbar term (``h_scrollbar_physical =
    (innerHeight - clientHeight) * DPR_page``) instead of the
    corrected ``* DPR_last``. Returns ``{"chrome_phys": float,
    "top_chrome_css": float, "h_scrollbar_physical": float}``. On
    the plan's fixture it gives chrome_phys 103.125 /
    top_chrome 82.5 vs the correct 100 / 80 - above the 1-px bar."""
    if first_slice is None:
        first_slice = _slice_at_point(slices, client_rect[0], client_rect[1])
    if last_slice is None:
        last_slice = _slice_at_point(
            slices, client_rect[0], client_rect[3] - 1)
    s_first = first_slice["scale"]
    s_last = last_slice["scale"]
    dpr_first = s_first * float(z)
    h_scrollbar = (
        max(0.0, float(inner_height_css) - float(viewport_height_css))
        * float(dpr_page)
    )
    column = [s for s in slices if s["left"] == first_slice["left"]]
    total = 0.0
    for sl in column:
        if sl is first_slice:
            continue
        scale = sl["scale"]
        if scale is None:
            continue
        viewport_phys = sl["height"]
        if sl is last_slice and first_slice is not last_slice:
            viewport_phys = sl["height"] - h_scrollbar
        total += viewport_phys / (scale * float(z))
    chrome_phys = (
        first_slice["height"]
        - dpr_first * (float(viewport_height_css) - total)
        - (h_scrollbar if first_slice is last_slice else 0.0)
    )
    return {
        "chrome_phys": chrome_phys,
        "top_chrome_css": chrome_phys / s_first,
        "h_scrollbar_physical": h_scrollbar,
    }


def legacy_plan6_derivation(client_height_phys, viewport_height_css,
                            dpr_page, s_window, h_scrollbar_physical=0.0):
    """Plan 6's derivation (discrimination only): the blended
    full-window height divided by the window's S -
    ``(H_phys - h_scrollbar - viewport_CSS * DPR) / S_window``.
    Returns ``top_chrome_css`` as a float. On the vertical-straddle
    fixture it gives 160 vs the true 80 (80 CSS px too large)."""
    return (
        float(client_height_phys) - float(h_scrollbar_physical)
        - float(viewport_height_css) * float(dpr_page)
    ) / float(s_window)


def legacy_plan4_assembly(top_chrome_css, dom_css, dpr):
    """Plan 4's assembly (P-1, discrimination only):
    ``(top_chrome_CSS + dom_CSS) * DPR`` - the chrome term multiplied
    by DPR instead of S. Returns the float y-offset. At S=1.25,
    C=80, dom=400 it gives 660 at Z=1.1 (true 650, 10 px too far
    down) and 900 at Z=1.5 (true 850, 50 px too far down)."""
    return (float(top_chrome_css) + float(dom_css)) * float(dpr)


# ---------------------------------------------------------------------------
# Two-step per-element S (Q-15)
# ---------------------------------------------------------------------------

def _two_step_scale(approx_point, compute, table=None):
    """The flip-bounded two-step (Q-15). ``compute(monitor)`` returns
    the element centre ``(x, y)`` for a monitor (its S); the
    returned point's monitor is re-resolved, and a flip recomputes
    with the new monitor. Two flips refuse with
    "monitor ambiguous - re-observe" - so a fake that flips the
    monitor twice never settles. Returns ``{"refused": False,
    "monitor": dict, "scale": float, "point": (x, y), "flips": int}``
    or a refusal dict.

    Note on the plan's "at most two MonitorFromPoint resolutions":
    the double-flip lab case can only be caught by resolving after
    the second recompute as well, so this loop performs up to three
    resolutions (approximate + one per recompute) while honouring
    the operative bound - a second flip refuses."""
    if table is None:
        table, _note = get_monitor_table()
    monitor = monitor_at_point(approx_point[0], approx_point[1], table)
    if monitor is None:
        return _refusal(
            f"no monitor contains the approximate centre "
            f"({approx_point[0]}, {approx_point[1]}) - re-observe",
            "Q-15",
        )
    flips = 0
    for _ in range(2):
        scale = _monitor_scale(monitor)
        if scale is None:
            return _refusal(REASON_SCALE_UNKNOWN, "T-6")
        point = compute(monitor, scale)
        resolved = monitor_at_point(point[0], point[1], table)
        if resolved is None:
            return _refusal(
                f"no monitor contains the recomputed centre "
                f"({point[0]}, {point[1]}) - re-observe",
                "Q-15",
            )
        if _monitor_id(resolved) == _monitor_id(monitor):
            return {
                "refused": False,
                "monitor": monitor,
                "scale": scale,
                "point": point,
                "flips": flips,
            }
        flips += 1
        monitor = resolved
    return _refusal(REASON_AMBIGUOUS, "Q-15")


# ---------------------------------------------------------------------------
# The conversion
# ---------------------------------------------------------------------------

def convert_viewport_to_screen(observation):
    """Convert a viewport CSS point to screen physical px (Plan 9
    section 1.1). ``observation`` is a dict::

        client_rect: (left, top, right, bottom) physical px
            (or ``hwnd`` + the window-rect provider seam)
        window_rect: (l, t, r, b) outer window rect, physical px
            (defaults to the client rect)
        visible_rect: (l, t, r, b) the target window's visible rect
            (defaults to the client rect)
        viewport_width_css, viewport_height_css: clientWidth/Height
        inner_width_css, inner_height_css: innerWidth/Height
            (defaults: the viewport sizes)
        scrollbar_width_css: innerWidth - clientWidth
            (defaults to inner_width_css - viewport_width_css)
        dpr_page: window.devicePixelRatio (includes page zoom)
        s_window: the window's S (GetDpiForWindow / 96)
        dom_x, dom_y: the element centre in viewport CSS px
        iframe_x, iframe_y: frame-chain offset in CSS px (default 0)
        dom_rect_css: (left, top, width, height) of the element in
            viewport CSS px (default: the centre as a point)
        left_border_css: chrome left of the viewport (default 0)
        observation_pass_id, latest_pass_id: R-10 ids
        dpi_awareness: "per-monitor-v2" | "per-monitor-v1" |
            "system" | "unaware" | "unknown" | None
        devtools_baseline, devtools_current: (height, width) deltas
        primary_monitor_id: the window's primary monitor identity
        monitor_table: an explicit table for this call (the override
            seam wins over it when set)

    Formula (every CSS term scaled by the factor that governs it on
    the monitor that renders it)::

        screen = client_origin
            + (left_border_CSS * S_el + (dom_x + iframe_x) * DPR_el,
               top_chrome_CSS * S_chrome + (dom_y + iframe_y) * DPR_el)

    ``DPR_el = S_el * Z``, ``Z = DPR_page / S_window``. ``S_chrome``
    is the S of the monitor containing the point (element_x,
    window_top). Non-straddling, this reduces exactly to Plan 6.

    Returns ``{"refused": False, "screen_x": float, "screen_y":
    float, "top_chrome_css": float, "left_border_check": float,
    "s_element": float, "dpr_element": float, "s_chrome": float,
    "element_monitor_id": str, "pass_id": ...}`` or a refusal dict.
    A refusal never yields a point: the caller re-observes."""
    obs = observation or {}

    # R-10: the observation must still be the latest.
    refusal = check_pass_id(
        obs.get("observation_pass_id"), obs.get("latest_pass_id"))
    if is_refusal(refusal):
        return refusal
    # P-10: the host must be per-monitor-aware.
    if "dpi_awareness" in obs:
        refusal = check_dpi_awareness(obs.get("dpi_awareness"))
        if is_refusal(refusal):
            return refusal
    # N-2: the DevTools delta must not have moved mid-goal.
    if "devtools_baseline" in obs or "devtools_current" in obs:
        refusal = check_devtools_delta(
            obs.get("devtools_baseline"), obs.get("devtools_current"))
        if is_refusal(refusal):
            return refusal

    try:
        s_window = float(obs["s_window"])
        dpr_page = float(obs["dpr_page"])
        dom_x = float(obs["dom_x"])
        dom_y = float(obs["dom_y"])
        iframe_x = float(obs.get("iframe_x", 0.0))
        iframe_y = float(obs.get("iframe_y", 0.0))
        left_border_css = float(obs.get("left_border_css", 0.0))
        viewport_w = float(obs["viewport_width_css"])
        viewport_h = float(obs["viewport_height_css"])
        inner_w = float(obs.get("inner_width_css", viewport_w))
        inner_h = float(obs.get("inner_height_css", viewport_h))
        scrollbar_w = float(
            obs.get("scrollbar_width_css", inner_w - viewport_w))
    except (KeyError, TypeError, ValueError):
        return _refusal(
            "observation is missing a conversion term - re-observe", "R-10")
    if s_window <= 0 or dpr_page <= 0:
        return _refusal(
            "window scale and page DPR must be positive - re-observe", "R-1")

    client_rect = obs.get("client_rect")
    if client_rect is None and obs.get("hwnd") is not None:
        client_rect = _window_client_rect(obs["hwnd"])
    if client_rect is None:
        return _refusal(
            "window client rect could not be read - re-observe", "P-3")
    try:
        client_rect = tuple(float(v) for v in client_rect)
    except (TypeError, ValueError):
        return _refusal(
            "window client rect could not be read - re-observe", "P-3")
    window_rect = obs.get("window_rect", client_rect)
    visible_rect = obs.get("visible_rect", client_rect)
    try:
        window_rect = tuple(float(v) for v in window_rect)
        visible_rect = tuple(float(v) for v in visible_rect)
    except (TypeError, ValueError):
        return _refusal(
            "window rect could not be read - re-observe", "P-3")

    table = obs.get("monitor_table")
    if _MONITOR_TABLE_OVERRIDE is not None:
        table, note = get_monitor_table()
    elif table is None:
        table, note = get_monitor_table()
    if not table:
        return _refusal(
            f"monitor table unreadable ({note}) - re-observe", "T-6")
    bounds = virtual_bounds(table)
    if bounds is None:
        return _refusal("virtual desktop bounds unreadable - re-observe", "U-3")

    # U-3: a window extending beyond the virtual desktop refuses -
    # never clamped onto a monitor it was not aimed at.
    if (window_rect[0] < bounds[0] or window_rect[1] < bounds[1]
            or window_rect[2] - 1 > bounds[2]
            or window_rect[3] - 1 > bounds[3]):
        return _refusal(
            f"window extends beyond the virtual desktop "
            f"(window rect {window_rect}, virtual bounds {bounds}) - "
            "move it fully on-screen",
            "U-3",
        )

    z = dpr_page / s_window
    slices = split_client_rect(client_rect, table)
    if not slices:
        return _refusal(
            "window is not on any monitor - re-observe", "R-1")

    check = left_border_check(
        slices, client_rect, viewport_w, scrollbar_w, z,
        primary_monitor_id=obs.get("primary_monitor_id"))
    if is_refusal(check):
        return check

    derived = derive_top_chrome(
        slices, client_rect, viewport_h, inner_h, z)
    if is_refusal(derived):
        return derived
    top_chrome_css = derived["top_chrome_css"]
    client_origin = (client_rect[0], client_rect[1])

    # Q-15 two-step: approximate centre with the window's S and the
    # page DPR, resolve the element's monitor, recompute with the
    # element's S and DPR_el = S_el * Z. Each computation uses the
    # final formula: the chrome term takes S_chrome, the S of the
    # monitor containing (element_x, window_top) - so the
    # approximate step already names the top monitor's S under a
    # vertical straddle, and the recompute only moves the dom term.
    def _compute(_monitor, s_el):
        dpr_el = s_el * z
        x = (client_origin[0] + left_border_css * s_el
             + (dom_x + iframe_x) * dpr_el)
        chrome_monitor = monitor_at_point(x, client_rect[1], table)
        s_chrome = _monitor_scale(chrome_monitor) \
            if chrome_monitor is not None else s_window
        if s_chrome is None:
            s_chrome = s_window
        y = (client_origin[1] + top_chrome_css * s_chrome
             + (dom_y + iframe_y) * dpr_el)
        return (x, y)

    approx = _compute(None, s_window)
    resolved = _two_step_scale(approx, _compute, table)
    if is_refusal(resolved):
        return resolved
    s_el = resolved["scale"]
    dpr_el = s_el * z
    element_monitor = resolved["monitor"]
    screen_x, _screen_y_approx = resolved["point"]

    # S_chrome: the monitor containing (element_x, window_top).
    chrome_point_monitor = monitor_at_point(
        screen_x, client_rect[1], table)
    if chrome_point_monitor is None:
        return _refusal(
            f"no monitor contains the chrome point "
            f"({screen_x}, {client_rect[1]}) - re-observe",
            "R-2",
        )
    s_chrome = _monitor_scale(chrome_point_monitor)
    if s_chrome is None:
        return _refusal(REASON_SCALE_UNKNOWN, "T-6")
    screen_y = client_origin[1] + top_chrome_css * s_chrome \
        + (dom_y + iframe_y) * dpr_el

    # Re-check the boundary with the APPROXIMATE rect (window S /
    # page DPR - the mapping that does not presume the element's
    # monitor): one element cannot have two S values. The refined
    # mapping would miss a genuine crossing - a rect straddling the
    # boundary maps its far corners onto one monitor under the
    # element's own S. Corners are inset by a hair so a rect merely
    # touching a boundary does not read as crossing it.
    dom_rect = obs.get("dom_rect_css")
    if dom_rect is not None:
        try:
            rx, ry, rw, rh = (float(v) for v in dom_rect)
        except (TypeError, ValueError):
            return _refusal(
                "element rect could not be read - re-observe", "Q-15")
        approx_chrome = monitor_at_point(approx[0], client_rect[1], table)
        s_chrome_approx = _monitor_scale(approx_chrome) \
            if approx_chrome is not None else s_window
        if s_chrome_approx is None:
            s_chrome_approx = s_window
        eps = 1e-6
        corners = [
            (rx + eps, ry + eps),
            (rx + rw - eps, ry + eps),
            (rx + eps, ry + rh - eps),
            (rx + rw - eps, ry + rh - eps),
        ]
        owners = set()
        for cx, cy in corners:
            px = (client_origin[0] + left_border_css * s_window
                  + cx * dpr_page)
            py = (client_origin[1] + top_chrome_css * s_chrome_approx
                  + cy * dpr_page)
            owner = monitor_at_point(px, py, table)
            owners.add(_monitor_id(owner) if owner is not None else None)
        if len(owners) > 1:
            return _refusal(REASON_BOUNDARY_CROSS, "Q-15")

    # Fence: refuse, never clamp, when the point is outside the
    # virtual desktop bounds or outside the target window's visible
    # rect - with the numbers in the refusal.
    if not (bounds[0] <= screen_x <= bounds[2]
            and bounds[1] <= screen_y <= bounds[3]):
        return _refusal(
            f"computed point ({screen_x:.2f}, {screen_y:.2f}) is "
            f"outside the virtual desktop bounds {bounds} - re-observe",
            "U-3",
        )
    if not (visible_rect[0] <= screen_x <= visible_rect[2] - 1
            and visible_rect[1] <= screen_y <= visible_rect[3] - 1):
        return _refusal(
            f"computed point ({screen_x:.2f}, {screen_y:.2f}) is "
            f"outside the target window's visible rect {visible_rect} "
            "- re-observe",
            "U-3",
        )

    return {
        "refused": False,
        "screen_x": round(float(screen_x), 2),
        "screen_y": round(float(screen_y), 2),
        "top_chrome_css": round(float(top_chrome_css), 2),
        "left_border_check": round(float(check["value"]), 2),
        "s_element": s_el,
        "dpr_element": dpr_el,
        "s_chrome": s_chrome,
        "element_monitor_id": _monitor_id(element_monitor),
        "pass_id": obs.get("observation_pass_id"),
    }
