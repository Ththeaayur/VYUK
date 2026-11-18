"""Multi-monitor / mixed DPI — §7 item 6 (parent draft).

Every coordinate means the same thing on every display: per-monitor
scale, per-monitor bounds, no cross-monitor drift.

A display target is::

    {monitor_id, bounds, scale, origin}

where ``monitor_id`` is the OS-stable monitor identity — the Windows
device name (``\\\\.\\DISPLAY2``) from ``GetMonitorInfoW`` — NOT the
transient index alone. The index is a spoken shorthand (``"second
monitor"``), confirmed against the CURRENT table on every call; after
a reorder, "monitor 2" may name a different device, and that is
correct behaviour, because the shorthand follows the table while
identity follows the device.

Rules (from the §7 item-6 definition, inheriting §0 + items 1–4):

* Convert against the CURRENT table at execution time, never a
  cached table from consent time. :func:`resolve_for_action` is the
  consumer seam: it reads a fresh table inside the call and accepts
  no caller table at all. A monitor resolved at consent time is
  re-resolved by identity at execution
  (:func:`re_resolve`); unresolvable refuses + re-asks.
* Refuse, never silently clamp or click scaled-wrong. The refusal
  classes are: unreadable scale refuses actions on that monitor;
  unknown monitor reference lists the current monitors (identity +
  position + scale) and asks; bounds violation refuses, never
  clamps the point onto another monitor. (The norm1000 grid itself
  still clamps a model overshoot to the grid edge — that is the
  pre-existing virtual-grid contract, not a monitor clamp: if the
  edge point is not on the target monitor, the bounds refusal
  still fires.)
* Scale, stated plainly. Norm1000 coordinates are virtual-desktop
  space, and under the app's per-monitor DPI awareness the virtual
  frame IS physical pixels — so the virtual mapping itself is not
  multiplied by scale (multiplying it would double-scale). Scale
  enters in exactly two honest places: (1) as a readability gate —
  a monitor whose effective DPI cannot be read cannot certify its
  frame, so actions on it refuse rather than risk a scaled-wrong
  click; (2) as arithmetic in :func:`logical_to_physical` /
  :func:`physical_to_logical`, for callers working in a monitor's
  logical frame (window placement APIs take logical coordinates).
  A 100% monitor is the identity in both places, so today's
  single-monitor behaviour is preserved exactly.
* Display configuration changes are OUT of scope: the assistant
  adapts to the arrangement, it never rearranges it (no resolution
  or arrangement changes, no SetWindowPos across monitors here —
  placement only computes the rect; driving it is the consumer's).

Backends: :func:`read_table` / :func:`resolve_for_action` take an
optional ``backend`` implementing the monitor protocol
(``enumerate()`` returning raw monitor dicts). The default backend
reads the live Windows APIs with lazy imports, so importing this
module needs only the standard library. Tests drive the protocol
with a fake backend; live multi-monitor + mixed-DPI behaviour on
real Windows stays field work and is not claimed here.

Deliberately NOT built here (named follow-ups, not silent gaps):
no funnel tool registration (the registry count and the G2
snapshot are untouched by this build), no coordinator consumer, no
migration of the two existing monitor resolvers
(``core.skills_window_control._resolve_monitor`` and the capture path's
resolver) onto this engine — that convergence pass runs later
with their suites as acceptance — and no mark-coordinate consumer
(the perception adapter's marks gain monitor validation when the
funnel consumer wires :func:`resolve_for_action` in).

Dependency note, not a claim: the norm1000 capture-freshness
investigation LANDED 2026-11-03 as the crop-grid refusal in
core/desktop/cursor.py (_capture_freshness_refusal) — for THIS
item's freshness rule it changes nothing, because this engine's
grid is virtual-desktop space, not capture-bound: a norm1000
point read off a stale capture still maps against the current
table, and a stale capture is still the capture layer's
problem to report (the cursor check refuses the capture-bound
cases; the monitor path's rule stays the fresh table).
"""

__all__ = [
    "canonical_sort",
    "describe_table",
    "format_scale",
    "logical_to_physical",
    "physical_to_logical",
    "place_rect_on_monitor",
    "read_table",
    "re_resolve",
    "receipt_line",
    "resolve_for_action",
    "resolve_monitor",
    "norm1000_to_physical",
    "validate_on_monitor",
    "virtual_bounds",
    "REOBSERVE_INSTRUCTION",
]

REOBSERVE_INSTRUCTION = (
    "Re-check the monitor arrangement and name the screen again."
)

#: Ordinal words the spoken shorthand understands ("second monitor").
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
}

#: Words that name a monitor by position, not by identity.
_POSITION_WORDS = ("left", "right", "top", "bottom")


# ---------------------------------------------------------------------------
# Table shape + sorting
# ---------------------------------------------------------------------------

def _monitor_id(entry):
    """The identity of a table entry: device name, never the index."""
    if isinstance(entry, dict):
        ident = entry.get("monitor_id") or entry.get("device") or ""
        return str(ident)
    return ""


def _device_suffix(device):
    """``DISPLAY2`` for ``\\\\.\\DISPLAY2`` — the spoken device shorthand."""
    text = str(device or "")
    return text.rsplit("\\", 1)[-1] if "\\" in text else text


def canonical_sort(table):
    """Table sorted primary-first, then left, then top, with index
    labels 1..n reassigned AFTER the sort.

    The sort mirrors the existing ``_monitor_details`` order so a
    spoken "monitor 2" means the same thing here as in the older
    display tools. Reassigning indices is deliberate: the index is
    a presentation label for the current table, never identity —
    identity (``monitor_id``) survives the sort untouched, so a
    reorder never renames a monitor, it only relabels shorthands.
    """
    ordered = sorted(
        (dict(m) for m in (table or [])),
        key=lambda m: (
            not bool(m.get("primary")),
            int(m.get("left", 0)),
            int(m.get("top", 0)),
        ),
    )
    for i, entry in enumerate(ordered, start=1):
        entry["index"] = i
    return ordered


def format_scale(scale):
    """``"150%"`` for 1.5, ``"unreadable"`` for None."""
    if scale is None:
        return "unreadable"
    try:
        return f"{round(float(scale) * 100)}%"
    except (TypeError, ValueError):
        return "unreadable"


def _scale_pair(monitor):
    """(scale_x, scale_y) for a monitor, each None when unreadable.

    Older entries may carry a single ``scale``; it fills both axes.
    """
    sx = monitor.get("scale_x", monitor.get("scale"))
    sy = monitor.get("scale_y", monitor.get("scale"))
    try:
        sx = float(sx) if sx is not None else None
    except (TypeError, ValueError):
        sx = None
    try:
        sy = float(sy) if sy is not None else None
    except (TypeError, ValueError):
        sy = None
    if sx is not None and sx <= 0:
        sx = None
    if sy is not None and sy <= 0:
        sy = None
    return sx, sy


def describe_table(table):
    """One line per monitor: identity + position + scale.

    This is the listing an unknown-reference refusal carries, so
    the user can answer "which screen?" from real data, never
    from a guessed fallback.
    """
    lines = []
    for m in canonical_sort(table):
        sx, sy = _scale_pair(m)
        scale = (
            format_scale(sx)
            if sx is not None and sx == sy
            else f"{format_scale(sx)} x {format_scale(sy)}"
        )
        primary = "primary, " if m.get("primary") else ""
        lines.append(
            f"{m.get('index')}: {_monitor_id(m)} ({primary}"
            f"{m.get('width')}x{m.get('height')} at "
            f"{m.get('left')},{m.get('top')}, {scale})"
        )
    return "; ".join(lines) if lines else "(no monitors readable)"


def virtual_bounds(table):
    """(min_x, min_y, max_x, max_y) inclusive union of the table,
    or None for an empty table. Never raises."""
    ordered = canonical_sort(table)
    if not ordered:
        return None
    try:
        min_x = min(int(m["left"]) for m in ordered)
        min_y = min(int(m["top"]) for m in ordered)
        max_x = max(int(m["left"]) + int(m["width"]) - 1 for m in ordered)
        max_y = max(int(m["top"]) + int(m["height"]) - 1 for m in ordered)
    except (KeyError, TypeError, ValueError):
        return None
    return min_x, min_y, max_x, max_y


def _position_label(monitor, table):
    """'left' / 'right' / 'top' / 'bottom' / 'primary' / '' for a
    monitor within its table — a receipt word, never identity."""
    ordered = canonical_sort(table)
    if not ordered:
        return ""
    if monitor.get("primary"):
        return "primary"
    labels = []
    try:
        lefts = [int(m["left"]) for m in ordered]
        tops = [int(m["top"]) for m in ordered]
        rights = [int(m["left"]) + int(m["width"]) - 1 for m in ordered]
        bottoms = [int(m["top"]) + int(m["height"]) - 1 for m in ordered]
        mine_l = int(monitor["left"])
        mine_t = int(monitor["top"])
        mine_r = mine_l + int(monitor["width"]) - 1
        mine_b = mine_t + int(monitor["height"]) - 1
    except (KeyError, TypeError, ValueError):
        return ""
    if mine_l == min(lefts) and len(set(lefts)) > 1:
        labels.append("left")
    if mine_r == max(rights) and len(set(rights)) > 1:
        labels.append("right")
    if mine_t == min(tops) and len(set(tops)) > 1:
        labels.append("top")
    if mine_b == max(bottoms) and len(set(bottoms)) > 1:
        labels.append("bottom")
    return labels[0] if labels else ""


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def _refusal_unknown(reference, table):
    return (
        f"Unknown monitor reference '{reference}'. Monitors now: "
        f"{describe_table(table)}. {REOBSERVE_INSTRUCTION}"
    )


def resolve_monitor(table, reference, current=None):
    """Resolve a spoken / written monitor reference against the
    CURRENT table. Returns (monitor, "") or (None, refusal).

    Reference forms, in order: device name (``\\\\.\\DISPLAY2`` or
    bare ``DISPLAY2``); index shorthand (``"2"``, ``"monitor 2"``,
    ``"screen 2"``, ``"second monitor"``); position words
    (``"left"``, ``"right"``, ``"top"``, ``"bottom"``,
    ``"primary"`` / ``"main"``); relative words (``"next"``,
    ``"other screen"`` / ``"other monitor"``) against ``current``
    (a monitor dict from the same table, or a monitor id).
    Anything else — including an ambiguous position tie and an
    "other" with more than two monitors — refuses listing the
    current monitors, never picks a first match.
    """
    ordered = canonical_sort(table)
    if not ordered:
        return None, (
            "No monitors could be read, so no screen can be named. "
            + REOBSERVE_INSTRUCTION
        )
    if reference is None:
        return None, _refusal_unknown(reference, ordered)
    if isinstance(reference, dict):
        wanted = _monitor_id(reference)
        for m in ordered:
            if _monitor_id(m) == wanted and wanted:
                return m, ""
        return None, _refusal_unknown(reference, ordered)
    text = str(reference).strip()
    lowered = text.lower()
    if not lowered:
        return None, _refusal_unknown(reference, ordered)

    # 1. Device name — the identity. Exact, or bare suffix.
    for m in ordered:
        device = _monitor_id(m)
        if device and (
            device.lower() == lowered
            or _device_suffix(device).lower() == lowered
        ):
            return m, ""

    # 2. Index shorthand: "2", "monitor 2", "screen 2", "display 2",
    #    "second monitor", "monitor two" is NOT understood (digits
    #    and ordinal words only — no word-number guessing). Either
    #    side's monitor/screen/display word is stripped, so both
    #    "monitor 2" and "second monitor" reach the same check.
    index = None
    cleaned = lowered
    for word in ("monitor ", "screen ", "display ", "monitor", "screen"):
        if cleaned.startswith(word):
            cleaned = cleaned[len(word):].strip()
            break
    for suffix in (" monitor", " screen", " display"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    if cleaned.isdigit():
        index = int(cleaned)
    elif cleaned in _ORDINALS:
        index = _ORDINALS[cleaned]
    if index is not None:
        for m in ordered:
            if m.get("index") == index:
                return m, ""
        return None, _refusal_unknown(reference, ordered)

    # 3. Position words. A trailing " monitor" / " screen" /
    #    " display" is stripped first, so "main monitor" and
    #    "primary screen" resolve the same as "main" / "primary".
    base = lowered
    for suffix in (" monitor", " screen", " display"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if base in ("primary", "main"):
        primaries = [m for m in ordered if m.get("primary")]
        if len(primaries) == 1:
            return primaries[0], ""
        return None, (
            f"'{reference}' matches {len(primaries)} primary monitors. "
            f"Monitors now: {describe_table(ordered)}. "
            + REOBSERVE_INSTRUCTION
        )
    if base in ("left", "leftmost"):
        return _resolve_position(ordered, reference, "left", min)
    if base in ("right", "rightmost"):
        return _resolve_position(ordered, reference, "right", max)
    if base in ("top", "topmost"):
        return _resolve_position(ordered, reference, "top", min)
    if base in ("bottom", "bottommost"):
        return _resolve_position(ordered, reference, "bottom", max)

    # 4. Relative words, against the current monitor.
    if base in ("next", "other"):
        current_monitor = None
        if isinstance(current, dict):
            wanted = _monitor_id(current)
            current_monitor = next(
                (m for m in ordered if _monitor_id(m) == wanted and wanted),
                None,
            )
        elif current is not None:
            wanted = str(current).strip().lower()
            current_monitor = next(
                (m for m in ordered
                 if _monitor_id(m).lower() == wanted
                 or _device_suffix(_monitor_id(m)).lower() == wanted),
                None,
            )
        if current_monitor is None:
            return None, (
                f"'{reference}' needs to know which screen you are on, "
                f"and that could not be determined. Monitors now: "
                f"{describe_table(ordered)}. " + REOBSERVE_INSTRUCTION
            )
        if base == "next":
            position = ordered.index(current_monitor)
            return ordered[(position + 1) % len(ordered)], ""
        others = [m for m in ordered if m is not current_monitor]
        if len(others) == 1:
            return others[0], ""
        return None, (
            f"'{reference}' is ambiguous with {len(ordered)} monitors. "
            f"Monitors now: {describe_table(ordered)}. "
            + REOBSERVE_INSTRUCTION
        )

    return None, _refusal_unknown(reference, ordered)


def _resolve_position(ordered, reference, key, pick):
    """One position word: the extreme monitor, or an ambiguous-tie
    refusal listing the tied monitors. Never the first of a tie."""
    try:
        if key == "left":
            values = [int(m["left"]) for m in ordered]
        elif key == "right":
            values = [int(m["left"]) + int(m["width"]) - 1 for m in ordered]
        elif key == "top":
            values = [int(m["top"]) for m in ordered]
        else:
            values = [int(m["top"]) + int(m["height"]) - 1 for m in ordered]
    except (KeyError, TypeError, ValueError):
        return None, _refusal_unknown(reference, ordered)
    extreme = pick(values)
    tied = [m for m, v in zip(ordered, values) if v == extreme]
    if len(tied) == 1:
        return tied[0], ""
    return None, (
        f"'{reference}' matches more than one monitor "
        f"({describe_table(tied)}). Monitors now: "
        f"{describe_table(ordered)}. " + REOBSERVE_INSTRUCTION
    )


def re_resolve(monitor_id, backend=None):
    """Re-resolve a consented monitor identity against a FRESH
    table — the consent-to-execution step for a reorder. Returns
    (monitor, "") or (None, refusal listing the current monitors
    + re-ask). Identity is the device name; a table that no longer
    contains it refuses, it never falls back to an index."""
    table, note = read_table(backend)
    if not table:
        return None, (
            f"Could not re-resolve monitor '{monitor_id}': {note} "
            + REOBSERVE_INSTRUCTION
        )
    for m in table:
        if _monitor_id(m) == str(monitor_id):
            return m, ""
    return None, (
        f"Monitor '{monitor_id}' is no longer present. Monitors now: "
        f"{describe_table(table)}. " + REOBSERVE_INSTRUCTION
    )


# ---------------------------------------------------------------------------
# Conversion + validation
# ---------------------------------------------------------------------------

def _norm1000_point(nx, ny, bounds):
    """Virtual-desktop point for a norm1000 pair, with the exact
    (dim - 1) scaling ``core.platform.windows._norm1000_to_pixels``
    uses — so norm1000 (500, 500) lands where move_fraction (0.5,
    0.5) of the same rectangle does. Out-of-grid values clamp to
    the grid edge (the pre-existing virtual-grid contract); the
    monitor bounds check below still has the final say."""
    min_x, min_y, max_x, max_y = bounds
    w = max_x - min_x + 1
    h = max_y - min_y + 1
    fx = min(max(float(nx), 0.0), 1000.0) / 1000.0
    fy = min(max(float(ny), 0.0), 1000.0) / 1000.0
    return (
        min_x + round(fx * (w - 1)),
        min_y + round(fy * (h - 1)),
    )


def validate_on_monitor(px, py, monitor):
    """True when (px, py) lies inside the monitor's reported rect
    (inclusive). Returns (ok, refusal): a violation refuses naming
    the monitor, the point, and the bounds — it never clamps."""
    try:
        left = int(monitor["left"])
        top = int(monitor["top"])
        right = left + int(monitor["width"]) - 1
        bottom = top + int(monitor["height"]) - 1
        x, y = int(px), int(py)
    except (KeyError, TypeError, ValueError):
        return False, (
            "The monitor's bounds could not be read, so the point "
            "cannot be validated on it. " + REOBSERVE_INSTRUCTION
        )
    if left <= x <= right and top <= y <= bottom:
        return True, ""
    return False, (
        f"Point ({x}, {y}) is not on monitor {_monitor_id(monitor)} "
        f"(bounds {left},{top} to {right},{bottom}), so nothing was "
        f"clicked there — a point is never moved onto another monitor "
        f"silently. " + REOBSERVE_INSTRUCTION
    )


def norm1000_to_physical(nx, ny, monitor, vbounds):
    """Norm1000 virtual-desktop point → physical pixels on the
    target monitor. Returns ((px, py), "") or (None, refusal).

    Order: scale readability gate first (an unreadable scale
    refuses — the frame cannot be certified), then the virtual
    mapping, then the bounds validation against the CURRENT
    monitor rect. A monitor-targeted point outside its monitor
    refuses; it is never clamped onto a neighbouring monitor.
    """
    sx, sy = _scale_pair(monitor)
    if sx is None or sy is None:
        return None, (
            f"Monitor {_monitor_id(monitor)} has an unreadable scale, "
            f"so actions on it refuse rather than risk a scaled-wrong "
            f"click. " + REOBSERVE_INSTRUCTION
        )
    if vbounds is None:
        return None, (
            "The virtual desktop bounds could not be read, so the "
            "norm1000 point cannot be converted. " + REOBSERVE_INSTRUCTION
        )
    try:
        point = _norm1000_point(nx, ny, vbounds)
    except (TypeError, ValueError):
        return None, "A norm1000 target needs both x and y as numbers."
    ok, refusal = validate_on_monitor(point[0], point[1], monitor)
    if not ok:
        return None, refusal
    return point, ""


def logical_to_physical(x, y, monitor):
    """A point in the monitor's logical frame → physical pixels.
    Returns ((px, py), "") or (None, refusal on unreadable scale).

    The monitor's logical rectangle is (left, top,
    width / scale_x, height / scale_y): a 150% monitor's logical
    width is two-thirds its physical width. A 100% monitor is the
    identity. This is where a monitor's scale is applied as
    arithmetic — for callers (window placement) whose APIs take
    logical coordinates.
    """
    sx, sy = _scale_pair(monitor)
    if sx is None or sy is None:
        return None, (
            f"Monitor {_monitor_id(monitor)} has an unreadable scale, "
            f"so logical coordinates cannot be converted on it. "
            + REOBSERVE_INSTRUCTION
        )
    try:
        left = int(monitor["left"])
        top = int(monitor["top"])
        lx, ly = float(x), float(y)
    except (KeyError, TypeError, ValueError):
        return None, "Logical coordinates must be numbers."
    return (
        left + round((lx - left) * sx),
        top + round((ly - top) * sy),
    ), ""


def physical_to_logical(px, py, monitor):
    """The inverse of :func:`logical_to_physical`."""
    sx, sy = _scale_pair(monitor)
    if sx is None or sy is None:
        return None, (
            f"Monitor {_monitor_id(monitor)} has an unreadable scale, "
            f"so physical coordinates cannot be converted on it. "
            + REOBSERVE_INSTRUCTION
        )
    try:
        left = int(monitor["left"])
        top = int(monitor["top"])
        x, y = int(px), int(py)
    except (KeyError, TypeError, ValueError):
        return None, "Physical coordinates must be whole numbers."
    return (
        left + round((x - left) / sx),
        top + round((y - top) / sy),
    ), ""


def place_rect_on_monitor(rect, from_monitor, to_monitor):
    """Where a window rect lands on another monitor, preserving
    its relative place and proportional size (the existing
    move-window semantics). Returns (rect, "") or (None, refusal).

    ``rect`` is (left, top, right, bottom) in physical pixels on
    the source monitor's work area. The placed rect is fitted
    inside the target monitor's work area by construction —
    proportional size never exceeds it, and the origin is pinned
    inside it — which is within-target fitting, NOT the forbidden
    cross-monitor clamp: a rect is never silently moved onto a
    monitor it was not aimed at. An unreadable scale on either
    monitor refuses.
    """
    for m in (from_monitor, to_monitor):
        sx, sy = _scale_pair(m)
        if sx is None or sy is None:
            return None, (
                f"Monitor {_monitor_id(m)} has an unreadable scale, "
                f"so window placement refuses. " + REOBSERVE_INSTRUCTION
            )
    try:
        wl, wt, wr, wb = (int(v) for v in rect)
        fl = int(from_monitor.get("work_left", from_monitor["left"]))
        ft = int(from_monitor.get("work_top", from_monitor["top"]))
        fr = int(from_monitor.get("work_right",
                                 from_monitor["left"] + from_monitor["width"] - 1))
        fb = int(from_monitor.get("work_bottom",
                                 from_monitor["top"] + from_monitor["height"] - 1))
        tl = int(to_monitor.get("work_left", to_monitor["left"]))
        tt = int(to_monitor.get("work_top", to_monitor["top"]))
        tr = int(to_monitor.get("work_right",
                               to_monitor["left"] + to_monitor["width"] - 1))
        tb = int(to_monitor.get("work_bottom",
                               to_monitor["top"] + to_monitor["height"] - 1))
    except (KeyError, TypeError, ValueError):
        return None, "A window rect and both monitors' work areas must be numbers."
    cur_w = max(1, fr - fl + 1)
    cur_h = max(1, fb - ft + 1)
    tgt_w = tr - tl + 1
    tgt_h = tb - tt + 1
    win_w = max(1, wr - wl + 1)
    win_h = max(1, wb - wt + 1)
    new_w = min(tgt_w, max(1, round(win_w * tgt_w / cur_w)))
    new_h = min(tgt_h, max(1, round(win_h * tgt_h / cur_h)))
    rel_x = (wl - fl) / cur_w
    rel_y = (wt - ft) / cur_h
    new_l = tl + round(rel_x * max(0, tgt_w - new_w))
    new_t = tt + round(rel_y * max(0, tgt_h - new_h))
    new_l = min(max(new_l, tl), tr - new_w + 1)
    new_t = min(max(new_t, tt), tb - new_h + 1)
    return (new_l, new_t, new_l + new_w - 1, new_t + new_h - 1), ""


def receipt_line(monitor, point):
    """Receipt words for a monitor-targeted action: identity +
    converted coordinates, never an index alone."""
    sx, sy = _scale_pair(monitor)
    scale = (
        format_scale(sx)
        if sx is not None and sx == sy
        else f"{format_scale(sx)} x {format_scale(sy)}"
    )
    return (
        f"monitor {_monitor_id(monitor)} "
        f"(index {monitor.get('index')}, {scale}) "
        f"at ({point[0]}, {point[1]})"
    )


# ---------------------------------------------------------------------------
# Execution-time seam + live reader
# ---------------------------------------------------------------------------

def read_table(backend=None):
    """The CURRENT monitor table, canonically sorted. Returns
    (table, note): note is "" on success, else the honest reason
    the table could not be read. Never raises; a backend failure
    yields an empty table, never a stale one."""
    raw = None
    if backend is not None:
        try:
            raw = backend.enumerate()
        except Exception as e:  # noqa: BLE001 — fake or broken backend
            return [], f"Could not read the monitor list: {e}."
    else:
        try:
            raw = _live_enumerate()
        except Exception as e:  # noqa: BLE001 — non-Windows / API failure
            return [], f"Could not read the monitor list: {e}."
    if raw is None:
        return [], "Could not read the monitor list."
    try:
        return canonical_sort(raw), ""
    except Exception as e:  # noqa: BLE001 — malformed entries
        return [], f"Could not read the monitor list: {e}."


def resolve_for_action(reference, nx, ny, backend=None, current=None):
    """The execution-time path for a monitor-targeted coordinate
    action. Reads a FRESH table inside this call — it takes no
    caller table, so a table cached at consent time can never
    reach dispatch — resolves the reference, converts norm1000
    with the target monitor's scale gate, and validates bounds.
    Returns ({"monitor": monitor, "point": (px, py)}, "") or
    (None, refusal). Nothing dispatches on a refusal."""
    table, note = read_table(backend)
    if not table:
        return None, (
            f"Could not resolve monitor '{reference}': {note} "
            + REOBSERVE_INSTRUCTION
        )
    monitor, refusal = resolve_monitor(table, reference, current=current)
    if monitor is None:
        return None, refusal
    point, refusal = norm1000_to_physical(
        nx, ny, monitor, virtual_bounds(table)
    )
    if point is None:
        return None, refusal
    return {"monitor": monitor, "point": point}, ""


def _live_enumerate():
    """Live Windows monitor table (device, rect, work rect,
    primary, effective-DPI scale). Raises on non-Windows or API
    failure — :func:`read_table` turns that into an honest note.
    A monitor whose DPI cannot be read stays in the table with
    scale None (listed, and refusing actions) rather than being
    silently dropped, which would renumber every shorthand."""
    import ctypes
    import sys
    from ctypes import wintypes

    if not sys.platform.startswith("win"):
        raise RuntimeError("Monitor enumeration uses Windows display APIs")
    user32 = ctypes.windll.user32
    shcore = ctypes.windll.shcore

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEX(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    monitors = []
    callback_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.POINTER(RECT), ctypes.c_double)

    def _collect(hmonitor, _hdc, _rect, _data):
        info = MONITORINFOEX()
        info.cbSize = ctypes.sizeof(MONITORINFOEX)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            w = info.rcWork
            scale_x = scale_y = None
            try:
                dpi_x = wintypes.UINT()
                dpi_y = wintypes.UINT()
                # MDT_EFFECTIVE_DPI = 0. GetDpiForMonitor returns an
                # HRESULT: 0 (S_OK) is success — a nonzero result
                # means the DPI is unreadable, not readable.
                hr = shcore.GetDpiForMonitor(
                    hmonitor, 0,
                    ctypes.byref(dpi_x), ctypes.byref(dpi_y))
                if hr == 0:
                    if dpi_x.value > 0:
                        scale_x = dpi_x.value / 96.0
                    if dpi_y.value > 0:
                        scale_y = dpi_y.value / 96.0
            except Exception:  # noqa: BLE001 — DPI unreadable: None, listed
                scale_x = scale_y = None
            monitors.append({
                "monitor_id": info.szDevice,
                "device": info.szDevice,
                "left": int(r.left),
                "top": int(r.top),
                "width": int(r.right) - int(r.left),
                "height": int(r.bottom) - int(r.top),
                "work_left": int(w.left),
                "work_top": int(w.top),
                "work_right": int(w.right) - 1,
                "work_bottom": int(w.bottom) - 1,
                "primary": bool(info.dwFlags & 1),
                "scale_x": scale_x,
                "scale_y": scale_y,
                "hmonitor": int(hmonitor),
            })
        return 1

    user32.EnumDisplayMonitors(0, None, callback_type(_collect), 0)
    return monitors
