"""Monitor wiring for cursor dispatch — §7 consumers batch 10.

The item-6 engine (:mod:`core.desktop.monitors`) is the oracle; this module
is the thin dispatch seam over it. It adds no resolver of its own
and changes no existing dispatch contract:

* Every call reads a FRESH monitor table inside the call
  (``monitors.read_table``) — never a consent-time or cached
  table. A reorder between consent and dispatch is honoured, not
  papered over.
* Validation is a gate, never a coordinate invention. A point
  inside the virtual-desktop union but in the gap between
  staggered monitors, or on a monitor whose scale is unreadable,
  refuses — it is never moved onto another monitor, and no
  fallback scale is ever borrowed from a neighbouring monitor.
* GRID WARNING (read before passing norm1000 here): norm1000 in
  the item-6 engine is VIRTUAL-DESKTOP space. That is NOT
  :func:`core.desktop.cursor.cursor_control`'s default grid, which maps
  across the last capture's rectangle (crop-aware). Callers must
  know which grid their coordinates are in: pass virtual-desktop
  norm1000 (as from ``monitor_action``'s virtual mapping) or
  true pixels here — never screenshot-crop coordinates.
* An unreadable table yields ``"unvalidated"``, not a refusal
  and not a validated pass: the caller falls back to its existing
  checks (virtual bounds, staleness). Validation that cannot run
  is reported as unrun, never silently passed as validated.
  This is what keeps single-monitor and headless-lab behaviour
  unchanged: where no table can be read, nothing new refuses.

Deliberately NOT built here: no funnel tool (registry/G2
untouched), no migration of the existing resolvers
(``core.skills_window_control._resolve_monitor``,
``core.skills_network._monitor_details``, the vision capture
crop geometry) — their semantics differ from item-6 (cycling
``'next'``, primary fallbacks, fragment matching, capture
rectangles) and migrating them would change behaviour, not
just move code. That convergence pass stays owed. Live
multi-monitor / mixed-DPI behaviour on real Windows stays
field work. The norm1000 capture-freshness investigation
LANDED 2026-11-03 as the crop-grid refusal in
core/desktop/cursor.py (_capture_freshness_refusal: live bounds
+ foreground-rect comparison, with stated residuals) — it does
not change this module, whose grid is virtual-desktop space,
not capture-bound. Nothing here claims the field work solved.
"""

from . import monitors  # Phase 3 S7a-2: monitors already in core/desktop/  # Phase 3 S7a: monitors moved to core/desktop/

__all__ = [
    "validate_point",
    "validate_pixel_on_monitor",
    "resolve_click_point",
    "validate_marks",
]


def _validate_point_on_table(px, py, table):
    """(monitor_or_None, status, note) against an already-read
    table. Status is ``"ok"`` or ``"refused"``. Containment is
    inclusive, matching :func:`monitors.validate_on_monitor`."""
    try:
        x, y = int(px), int(py)
    except (TypeError, ValueError):
        return None, "refused", (
            "A monitor-validated point needs whole-number pixels.")
    for entry in table:
        try:
            left = int(entry["left"])
            top = int(entry["top"])
            right = left + int(entry["width"]) - 1
            bottom = top + int(entry["height"]) - 1
        except (KeyError, TypeError, ValueError):
            continue
        if left <= x <= right and top <= y <= bottom:
            sx, sy = monitors._scale_pair(entry)
            if sx is None or sy is None:
                ident = (entry.get("monitor_id")
                         or entry.get("device") or "")
                return None, "refused", (
                    f"Monitor {ident} has an unreadable scale, so "
                    "actions on it refuse rather than risk a "
                    "scaled-wrong click. "
                    + monitors.REOBSERVE_INSTRUCTION)
            return entry, "ok", ""
    return None, "refused", (
        f"Point ({x}, {y}) is not on any monitor. Monitors now: "
        f"{monitors.describe_table(table)}. "
        + monitors.REOBSERVE_INSTRUCTION)


def validate_point(px, py, backend=None):
    """(monitor_or_None, status, note) for a true-pixel point
    against a FRESH table. Status is ``"ok"``, ``"refused"``, or
    ``"unvalidated"`` (no table could be read — the caller falls
    back to its existing checks). Never raises."""
    table, note = monitors.read_table(backend)
    if not table:
        return None, "unvalidated", (
            note or "Could not read the monitor list.")
    return _validate_point_on_table(px, py, table)


def validate_pixel_on_monitor(reference, px, py, backend=None,
                              current=None):
    """(True, "") when a true-pixel point lies on the NAMED
    monitor in a FRESH table with a readable scale, else
    (False, refusal). The point is never moved onto another
    monitor, and an unreadable scale on the named monitor
    refuses rather than borrowing a neighbour's scale."""
    try:
        x, y = int(px), int(py)
    except (TypeError, ValueError):
        return False, "x and y must be whole numbers (pixels)."
    table, note = monitors.read_table(backend)
    if not table:
        return False, (
            f"Could not resolve monitor '{reference}': {note} "
            + monitors.REOBSERVE_INSTRUCTION)
    monitor, refusal = monitors.resolve_monitor(
        table, reference, current=current)
    if monitor is None:
        return False, refusal
    sx, sy = monitors._scale_pair(monitor)
    if sx is None or sy is None:
        ident = (monitor.get("monitor_id")
                 or monitor.get("device") or "")
        return False, (
            f"Monitor {ident} has an unreadable scale, so actions "
            "on it refuse rather than risk a scaled-wrong click. "
            + monitors.REOBSERVE_INSTRUCTION)
    ok, refusal = monitors.validate_on_monitor(x, y, monitor)
    if not ok:
        return False, refusal
    return True, ""


def resolve_click_point(reference, nx, ny, backend=None, current=None):
    """The execution-time seam for monitor-targeted norm1000
    dispatch: :func:`monitors.resolve_for_action` against a FRESH
    table. Returns ({"monitor": monitor, "point": (px, py)}, "")
    or (None, refusal). Nothing dispatches on a refusal. The
    norm1000 grid here is virtual-desktop space (see the module
    docstring's grid warning)."""
    return monitors.resolve_for_action(
        reference, nx, ny, backend=backend, current=current)


def validate_marks(marks, backend=None):
    """Validate mark rectangles against a FRESH monitor table.

    Returns {"valid": [...], "flagged": [...], "table_note": ""}:
    valid marks are copies annotated with ``monitor_id`` (the OS
    device name); flagged entries are {"mark": copy, "reason"}
    for marks with no usable rectangle, marks whose centre is on
    no monitor, or marks on an unreadable-scale monitor. The
    input marks are never mutated. When no table can be read,
    valid carries the marks unchanged and unannotated, flagged
    is empty, and table_note carries the honest reason — the
    marks are UNVALIDATED, not valid and not invalid, and the
    caller falls back to its existing checks."""
    table, note = monitors.read_table(backend)
    if not table:
        return {
            "valid": [dict(m) for m in (marks or [])
                      if isinstance(m, dict)],
            "flagged": [],
            "table_note": note or "Could not read the monitor list.",
        }
    valid, flagged = [], []
    for mark in (marks or []):
        if not isinstance(mark, dict):
            flagged.append({
                "mark": mark,
                "reason": "not a mark record — no rectangle to validate",
            })
            continue
        rect = mark.get("rect")
        try:
            left, top, right, bottom = (int(v) for v in rect)
        except (TypeError, ValueError):
            flagged.append({
                "mark": dict(mark),
                "reason": (f"Mark {mark.get('id')} has no usable "
                           "rectangle — re-observe."),
            })
            continue
        cx, cy = (left + right) // 2, (top + bottom) // 2
        monitor, status, reason = _validate_point_on_table(
            cx, cy, table)
        if status != "ok":
            flagged.append({"mark": dict(mark), "reason": reason})
            continue
        annotated = dict(mark)
        annotated["monitor_id"] = (
            monitor.get("monitor_id") or monitor.get("device") or "")
        valid.append(annotated)
    return {"valid": valid, "flagged": flagged, "table_note": ""}
