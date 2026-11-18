"""C7 crop producer: region crops cut from the retained sent image.

C7 ("Verify the spot, not the snapshot") has two halves: the
verifier's region compare and the approval dialog showing the
STORED crop. The dialog half is built (gui/window_services_confirm
builders accept ``stored_crop``); this module is the PRODUCER
half — the code that turns a point step's coordinates into the
grayscale region grid the verifier and the dialog consume.

Pixel supply: VisionManager retains the SENT image (the
thumbnailed frame the brain actually saw, grayscale) in a
SINGLE slot, overwritten per capture_screen call — never a
frame history. A region is cut from that slot on demand, only
when the step names the capture the slot holds. Everything
else is an honest None: no id, id mismatch, no single primary
point, point outside the sent image, or unmeasurable geometry
all mean "no crop", never a fabricated, neighbouring, or
freshly re-captured region.

Coordinate mapping (all from the RETAINED record, never live
probes, so a cut cannot drift when the world moves between
capture and cut):
- norm1000 (the default for cursor_control x/y): the reference
  rect mirrors cursor_geometry._norm1000_reference's branches
  from record fields only (crop rect when cropped, else the
  virtual bounds when they match the screen size, else the
  full screen), scaled by the canonical
  platform.windows._norm1000_to_pixels, then true pixels map
  to sent-image pixels by the record's crop/screen-to-image
  ratios.
- pixels (cursor_control with coords="pixels", mouse_control
  x/y, single-pair points): true pixels map straight to
  sent-image pixels by the same ratios.

Single-spot rule: a step names ONE spot (x/y, or a single
points pair) or it names none for crop purposes. Multi-point
steps (waypoint paths, drags with x2/y2) attach nothing — one
region cannot honestly stand for a path.
"""

from __future__ import annotations

REGION_HALF_SIDE = 48


def _reference_rect(record):
    """(ox, oy, w, h) true-pixel reference rect, or None.

    Mirrors _norm1000_reference's branches from record fields
    only: crop rect when the capture was cropped, else the
    virtual bounds when they match the screen size, else the
    full screen. No live probes — the retained record is the
    whole truth for the cut.
    """
    try:
        if record.get("cropped"):
            cl = int(record["crop_left"])
            ct = int(record["crop_top"])
            cw = int(record["crop_width"])
            ch = int(record["crop_height"])
            if cw > 0 and ch > 0:
                return (cl, ct, cw, ch)
        sw = int(record["screen_width"])
        sh = int(record["screen_height"])
        vb = record.get("virtual_bounds")
        if vb is not None and len(vb) == 4:
            min_x, min_y, max_x, max_y = (int(v) for v in vb)
            if max_x - min_x + 1 == sw and max_y - min_y + 1 == sh:
                return (min_x, min_y, sw, sh)
        if sw > 0 and sh > 0:
            return (0, 0, sw, sh)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _sent_point(record, tx, ty):
    """True pixel -> sent-image pixel (float pair), or None."""
    try:
        iw = int(record["image_width"])
        ih = int(record["image_height"])
        if iw <= 0 or ih <= 0:
            return None
        if record.get("cropped"):
            cl = int(record["crop_left"])
            ct = int(record["crop_top"])
            cw = int(record["crop_width"])
            ch = int(record["crop_height"])
            if cw <= 0 or ch <= 0:
                return None
            return ((tx - cl) * iw / cw, (ty - ct) * ih / ch)
        sw = int(record["screen_width"])
        sh = int(record["screen_height"])
        if sw <= 0 or sh <= 0:
            return None
        return (tx * iw / sw, ty * ih / sh)
    except (KeyError, TypeError, ValueError):
        return None


def cut_region(slot, x, y, mode="norm1000", half_side=REGION_HALF_SIDE):
    """Grayscale region grid for a point on the retained frame.

    ``slot`` is a VisionManager last-sent-frame dict
    {capture_id, width, height, gray (bytes, row-major),
    record}. ``(x, y)`` is norm1000 by default, true pixels
    when mode == "pixels". Returns rows of ints, or None on
    any unusable input, id-independent (the caller checks the
    capture id) — a point outside the sent image, or a region
    that clamps to empty, is None, never a neighbouring
    region.
    """
    try:
        if not isinstance(slot, dict):
            return None
        width = int(slot["width"])
        height = int(slot["height"])
        gray = slot["gray"]
        record = slot["record"]
        if width <= 0 or height <= 0 or not isinstance(record, dict):
            return None
        if len(gray) != width * height:
            return None
        if mode == "norm1000":
            ref = _reference_rect(record)
            if ref is None:
                return None
            from ..platform.windows import _norm1000_to_pixels
            rx, ry = _norm1000_to_pixels(float(x), float(y),
                                         ref[2], ref[3])
            tx, ty = ref[0] + rx, ref[1] + ry
        elif mode == "pixels":
            tx, ty = float(x), float(y)
        else:
            return None
        sent = _sent_point(record, tx, ty)
        if sent is None:
            return None
        cx, cy = int(round(sent[0])), int(round(sent[1]))
        if cx < 0 or cx >= width or cy < 0 or cy >= height:
            # Centre outside the sent image: honest absence.
            # Clamping here would substitute a neighbouring
            # region for the point's own — never do that.
            return None
        hs = int(half_side)
        if hs < 0:
            return None
        x0 = max(0, cx - hs)
        x1 = min(width, cx + hs + 1)
        y0 = max(0, cy - hs)
        y1 = min(height, cy + hs + 1)
        if x1 <= x0 or y1 <= y0:
            return None
        return [list(gray[row * width + x0:row * width + x1])
                for row in range(y0, y1)]
    except (KeyError, TypeError, ValueError):
        return None


def _numeric_pair(a, b):
    """(float, float) when both are real numbers, else None."""
    if isinstance(a, bool) or isinstance(b, bool):
        return None
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return None
    return (float(a), float(b))


def primary_point_for_step(tool, step_args):
    """One (x, y, mode) for a point step, or None.

    cursor_control: x/y (coords mode, norm1000 by default), or
    a single points pair (true pixels by schema contract).
    mouse_control: x/y are true screen pixels. Anything else
    — window_control (no coordinates), multi-point paths,
    drags with x2/y2, unknown modes — is not a single spot
    and yields None, never a guessed point.
    """
    if not isinstance(step_args, dict):
        return None
    tool = str(tool or "").strip()
    if tool == "cursor_control":
        mode = str(step_args.get("coords") or "norm1000").strip().lower()
        if mode not in ("norm1000", "pixels"):
            return None
        pair = _numeric_pair(step_args.get("x"), step_args.get("y"))
        if pair is not None:
            if _numeric_pair(step_args.get("x2"),
                             step_args.get("y2")) is not None:
                return None  # drag: two spots, not one
            return (pair[0], pair[1], mode)
        points = step_args.get("points")
        if isinstance(points, (list, tuple)) and len(points) == 1:
            only = points[0]
            if isinstance(only, (list, tuple)) and len(only) == 2:
                pair = _numeric_pair(only[0], only[1])
                if pair is not None:
                    return (pair[0], pair[1], "pixels")
            if isinstance(only, dict):
                pair = _numeric_pair(only.get("x"), only.get("y"))
                if pair is not None:
                    return (pair[0], pair[1], "pixels")
        return None
    if tool == "mouse_control":
        pair = _numeric_pair(step_args.get("x"), step_args.get("y"))
        if pair is not None:
            return (pair[0], pair[1], "pixels")
        return None
    return None


def stored_crop_for_step(tool, step_args, slot):
    """Stored region grid for a step, or None (honest absence).

    Serves ONLY when the step names a capture id AND that id
    is the retained slot's id AND the step has one primary
    point. Every other case — no id, id mismatch, no slot,
    multi-spot step, unmeasurable geometry — is None. The
    caller attaches the grid only when this returns one, so
    a step without a crop behaves exactly as before.
    """
    try:
        if not isinstance(step_args, dict) or not isinstance(slot, dict):
            return None
        sid = step_args.get("source_capture_id")
        if sid is None:
            return None
        if str(sid) != str(slot.get("capture_id")):
            return None
        point = primary_point_for_step(tool, step_args)
        if point is None:
            return None
        return cut_region(slot, point[0], point[1], mode=point[2])
    except Exception:  # noqa: BLE001 — a crop never raises
        return None
