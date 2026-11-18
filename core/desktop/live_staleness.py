"""Live staleness check (P1 delivery ledger + P3 helper), companion
to point_crop.py.

Design: LIVE_VERIFIER_COVERAGE_DESIGN.md at the tree root (the
live-loop verifier coverage design, 2026-11-05). The honest
insertion for live point actions is a STALENESS check, not a
binding check: binding a live point to a frame is impossible
with today's records (the vision slot holds the latest CAPTURED
frame, not the frame the model read the point off), so this
module refuses only on positive evidence the VIEW changed since
the model last saw it — never a claim the point is right.

P1 — Delivery ledger: a SINGLE slot (no history) recording the
last frame actually DELIVERED to the live session, written only
from ``send_image_frame`` when it returns "delivered". Pixels
come from the bytes actually sent (authoritative for "what the
model saw"); geometry/capture id come from the capture record
at send time (best-effort link — the compare itself is
pixel-only, so a stale geometry link can never manufacture a
verdict). The ledger is deliberately separate from the vision
slot: the slot fills at capture time (and a capture that never
delivers still fills it); the ledger fills at delivery time.

P3 — Fresh grab + PIL-resize downsample + compare: the fresh
frame comes from ``capture_screen()`` with defaults (marks off,
matching the Share button / tool-sink delivery paths) and both
frames are resized with PIL (C speed) to a small common grid,
then compared with the C7 compare SHAPE (mean absolute
difference + fraction of pixels changed a lot) under their OWN
named thresholds. This module does NOT import control_existence
and does NOT call normalise_grid: normalise_grid's pure-Python
block averaging costs ~80-110 ms per full-frame downsample
(measured in the design batch), and the C7 region constants
were tuned for 96 px regions, not full frames. Threshold
defaults below start from the C7 region values as LAB
PLACEHOLDERS — P5 (real-Windows field legs) must tune them on
real captures before the stage is trusted; until P5 lands, the
stage is lab-only even though it runs.

Pass-through posture (the verifier's positive-evidence-only
rule): no baseline, grab failed, unusable grids, over budget,
or any check error all PASS THROUGH labelled — never a fake
refusal. A slow check degrades to the geometry floor; slowness
is not evidence of staleness. The stage (P4, in the funnel)
owns the refusal; this module only returns verdicts and never
raises (check_staleness catches everything into "unchecked").
"""

from __future__ import annotations

import io
import itertools
import time

from PIL import Image

# P3 grid + budget (design §3.2/§5.3; budget is a PROPOSAL to be
# pinned by measurement on real Windows in P5).
STALENESS_GRID_WIDTH = 160
STALENESS_GRID_HEIGHT = 90
STALENESS_BUDGET_MS = 500.0

# P3 thresholds — LAB DEFAULTS, P5-tuning-pending (see module
# docstring). They start from the C7 region values
# (MEAN_ABS_DIFF_MAX 12.0, LARGE_DIFF 48, FRAC_LARGE_MAX 0.10)
# as named parameters, never as a silent reuse: P5 must retune
# them on real captures (animated vs static screens, false-
# refusal rate) before the stage is trusted.
STALENESS_MEAN_MAX = 12.0
STALENESS_LARGE_DIFF = 48
STALENESS_FRAC_LARGE_MAX = 0.10

# P4 scope — live-origin absolute-target point actions only
# (design §3.3). Relative actions carry no absolute target to
# stale-check; mark clicks are already guarded in-handler;
# window_control carries no coordinates.
_CURSOR_ABSOLUTE_ACTIONS = frozenset({
    "move", "click", "double_click", "right_click", "middle_click",
    "drag", "beacon",
})
_MOUSE_ABSOLUTE_ACTIONS = frozenset({
    "move", "click", "double_click", "right_click",
})


# ---------------------------------------------------------------------------
# P1 — delivery ledger (single slot, no history)
# ---------------------------------------------------------------------------

_LEDGER: dict = {"frame": None}
_DELIVERY_SEQ = itertools.count(1)


def _decode_gray_grid(image_bytes):
    """Decode image bytes to (width, height, grayscale bytes).

    Returns None when the bytes are not a decodable image — an
    undecodable delivery records nothing, never a fake frame.
    """
    try:
        if not image_bytes:
            return None
        with Image.open(io.BytesIO(image_bytes)) as img:
            gray = img.convert("L")
            return (int(gray.width), int(gray.height), gray.tobytes())
    except Exception:  # noqa: BLE001 — undecodable bytes are
        return None  # not a frame; the caller records nothing


def record_delivery(image_bytes, capture_record=None) -> bool:
    """Record one DELIVERED frame in the single-slot ledger.

    Call only when the send actually returned "delivered".
    Returns True when a frame was recorded, False when the bytes
    were undecodable (nothing recorded — the previous baseline,
    if any, stands). Overwrites any previous frame: the slot
    holds the LAST delivered frame only, never a history.
    """
    decoded = _decode_gray_grid(image_bytes)
    if decoded is None:
        return False
    width, height, gray = decoded
    record = dict(capture_record) if isinstance(
        capture_record, dict) else None
    _LEDGER["frame"] = {
        "delivery_seq": next(_DELIVERY_SEQ),
        "capture_id": record.get("capture_id") if record else None,
        "geometry": record,
        "width": width,
        "height": height,
        "gray": gray,
    }
    return True


def last_delivered_frame():
    """A copy of the last delivered frame, or None.

    The gray bytes are immutable, so the copy shares them safely;
    the geometry dict is copied so callers cannot mutate the slot.
    """
    frame = _LEDGER.get("frame")
    if frame is None:
        return None
    out = dict(frame)
    geometry = frame.get("geometry")
    out["geometry"] = dict(geometry) if isinstance(
        geometry, dict) else None
    return out


def clear_delivery_ledger() -> None:
    """Empty the ledger (tests only — production never clears it
    except by overwriting on the next delivery)."""
    _LEDGER["frame"] = None


# ---------------------------------------------------------------------------
# P4 scope filter
# ---------------------------------------------------------------------------

def _is_number(value) -> bool:
    """True for JSON-number coordinates (int/float, not bool).

    Non-numeric coordinates are not absolute targets for the
    filter's purposes — the funnel's validator refuses them
    later; the stage must not spend a screen grab on garbage.
    """
    return isinstance(value, (int, float)) and not isinstance(
        value, bool)


def _has_absolute_coords(args) -> bool:
    """True when the call names an absolute target: x/y pair or
    fx/fy pair (cursor fractions are absolute positions, not
    deltas). A bare click with no coordinates clicks wherever
    the cursor already is — no absolute target, out of scope.
    """
    try:
        if _is_number(args.get("x")) and _is_number(args.get("y")):
            return True
        if _is_number(args.get("fx")) and _is_number(args.get("fy")):
            return True
        return False
    except Exception:  # noqa: BLE001 — malformed args are
        return False  # not a target; the validator says so later


def _has_point_target(args) -> bool:
    """True when args carry a parseable raw point target ('x,y'
    with whole numbers) — the click_anywhere / browser_click
    raw-coordinates rung. Malformed reads as False (the handler
    refuses it with raw-point-shape); never a raise."""
    try:
        target = args.get("target")
        if not isinstance(target, str):
            return False
        parts = [p.strip() for p in target.split(",")]
        if len(parts) != 2:
            return False
        int(parts[0])
        int(parts[1])
        return True
    except Exception:  # noqa: BLE001 — malformed is out of scope
        return False


def is_live_point_action(tool, args) -> bool:
    """True only for in-scope live point actions (design §3.3).

    Total by construction: any malformed input reads as
    out-of-scope, never a raise — the filter must never break a
    funnel call, and out-of-scope calls proceed exactly as today.
    """
    try:
        if not isinstance(args, dict):
            return False
        if tool == "cursor_control":
            action = str(args.get("action") or "").strip().lower()
            return action in _CURSOR_ABSOLUTE_ACTIONS and \
                _has_absolute_coords(args)
        if tool == "mouse_control":
            action = str(args.get("action") or "").strip().lower()
            return action in _MOUSE_ABSOLUTE_ACTIONS and \
                args.get("x") is not None and args.get("y") is not None \
                and _is_number(args.get("x")) and _is_number(args.get("y"))
        if tool == "click_anywhere":
            # Voice-control batch (2026-11-16, C4): a raw-point
            # click/type/press on a live route acts at absolute
            # screen coordinates — the same stale-view hazard as
            # the cursor/mouse absolute actions. The named rungs
            # (dom/uia/mark/ocr/selector) re-resolve the element
            # against the live view, so only the raw rung is in
            # scope.
            action = str(args.get("action") or "").strip().lower()
            if action not in ("click", "type", "press"):
                return False
            if str(args.get("by") or "").strip().lower() != "point":
                return False
            return _has_point_target(args)
        if tool == "browser_click":
            # Voice-control batch (2026-11-16, C4): same hazard
            # — by='point' clicks whatever lives at a viewport
            # point, with no element to re-resolve. Any click
            # kind is in scope: a hover on a stale view
            # misleads the same way a click does.
            if str(args.get("by") or "").strip().lower() != "point":
                return False
            return _has_point_target(args)
        return False
    except Exception:  # noqa: BLE001 — see above
        return False


# ---------------------------------------------------------------------------
# P3 — resize + compare + check
# ---------------------------------------------------------------------------

def _resize_to_grid(width, height, gray_bytes):
    """Resize a grayscale frame to the small common grid.

    PIL BILINEAR (C speed) to the fixed STALENESS_GRID size —
    both sides get the identical treatment, so the compare is
    consistent even when the two frames differ in size or aspect
    (a geometry change then reads as content change, which is
    the honest verdict: the view is not the view that was
    shared). Returns rows of ints, or None when unusable.
    """
    try:
        if not width or not height or not gray_bytes:
            return None
        if len(gray_bytes) != width * height:
            return None
        img = Image.frombytes("L", (int(width), int(height)),
                              bytes(gray_bytes))
        small = img.resize(
            (STALENESS_GRID_WIDTH, STALENESS_GRID_HEIGHT),
            Image.Resampling.BILINEAR)
        pixels = list(small.tobytes())
        rows = [pixels[i * STALENESS_GRID_WIDTH:
                       (i + 1) * STALENESS_GRID_WIDTH]
                for i in range(STALENESS_GRID_HEIGHT)]
        return rows
    except Exception:  # noqa: BLE001 — unusable grids never
        return None  # compare as a match, nor raise


def _compare_grids(stored_rows, fresh_rows, *, mean_max, large_diff,
                   frac_large_max):
    """C7 compare shape on two small grids (same dimensions).

    Returns {"match", "mean_abs_diff", "frac_large", "reason"}.
    Dimension mismatch or unusable input is a MISMATCH (positive
    evidence the view changed shape), never a crash — the caller
    decides whether a mismatch refuses (it does: a changed view
    is exactly what the stage refuses on).
    """
    try:
        if not stored_rows or not fresh_rows:
            return {"match": False, "mean_abs_diff": None,
                    "frac_large": None,
                    "reason": "a grid is empty — the view cannot "
                              "be compared."}
        if len(stored_rows) != len(fresh_rows) or \
                len(stored_rows[0]) != len(fresh_rows[0]):
            return {"match": False, "mean_abs_diff": None,
                    "frac_large": None,
                    "reason": "grid dimensions differ — the view "
                              "changed shape."}
        total = 0
        large = 0
        count = 0
        for row_a, row_b in zip(stored_rows, fresh_rows):
            for va, vb in zip(row_a, row_b):
                diff = abs(int(va) - int(vb))
                total += diff
                if diff > large_diff:
                    large += 1
                count += 1
        mean = total / count if count else 0.0
        frac = large / count if count else 0.0
        if mean <= mean_max and frac <= frac_large_max:
            return {"match": True, "mean_abs_diff": mean,
                    "frac_large": frac,
                    "reason": "view matches within tolerance "
                              "(mean diff %.1f, %.0f%% of pixels "
                              "changed a lot)." % (mean, frac * 100)}
        return {"match": False, "mean_abs_diff": mean,
                "frac_large": frac,
                "reason": "view changed (mean diff %.1f exceeds "
                          "%.1f, or %.0f%% of pixels changed a lot "
                          "exceeds %.0f%%)." % (
                              mean, mean_max, frac * 100,
                              frac_large_max * 100)}
    except Exception:  # noqa: BLE001 — compare failures are
        return {"match": False, "mean_abs_diff": None,
                "frac_large": None,
                "reason": "compare failed — the view cannot be "
                          "trusted as unchanged."}


def _default_grab():
    """Fresh grab through the standard capture path, marks off.

    capture_screen() defaults are already marks-off (marks=False,
    include_cursor=False, mark=None) and match the Share button
    / tool-sink delivery paths. Returns (width, height, gray
    bytes) or None when the grab fails. NOTE: the grab fills the
    vision slot (latest capture, by definition) but NEVER the
    delivery ledger — only an actual delivery writes the ledger.
    """
    try:
        from .vision_manager import vision_manager
        img_bytes, _mime = vision_manager.capture_screen()
        return _decode_gray_grid(img_bytes)
    except Exception:  # noqa: BLE001 — a failed grab is a
        return None  # pass-through, never a stage crash


def check_staleness(*, ledger_frame=None, grab=None,
                    budget_ms=STALENESS_BUDGET_MS,
                    mean_max=STALENESS_MEAN_MAX,
                    large_diff=STALENESS_LARGE_DIFF,
                    frac_large_max=STALENESS_FRAC_LARGE_MAX):
    """Compare a fresh grab against the last DELIVERED frame.

    Returns {"verdict", "reason", "mean_abs_diff", "frac_large",
    "elapsed_ms"} where verdict is one of:
    - "fresh": the view matches within tolerance — proceed.
    - "stale": positive evidence the view changed — refuse.
    - "unchecked": no baseline, grab failed, unusable grids,
      over budget, or any check error — PASS THROUGH labelled,
      never a refusal (an unverifiable world passes through;
      the geometry floor still applies).

    Never raises. ``ledger_frame`` / ``grab`` are injectable
    seams for tests (defaults: the live ledger / standard
    capture path). The budget is measured wall time AFTER the
    grab and AFTER the compare — a slow grab still costs its
    wall time, but it never refuses: slowness is not evidence
    of staleness.
    """
    t0 = time.monotonic()

    def _unchecked(reason):
        return {"verdict": "unchecked", "reason": reason,
                "mean_abs_diff": None, "frac_large": None,
                "elapsed_ms": (time.monotonic() - t0) * 1000.0}

    try:
        baseline = ledger_frame if ledger_frame is not None \
            else last_delivered_frame()
        if baseline is None:
            return _unchecked("no-baseline: no frame has been "
                              "delivered to the live session yet")
        grab_fn = grab if grab is not None else _default_grab
        fresh = grab_fn()
        if fresh is None:
            return _unchecked("grab-failed: the fresh screen grab "
                              "failed or was undecodable")
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if elapsed_ms > budget_ms:
            return _unchecked("slow-grab: grab took %.0f ms, over "
                              "the %.0f ms budget" % (
                                  elapsed_ms, budget_ms))
        try:
            stored_rows = _resize_to_grid(
                baseline["width"], baseline["height"],
                baseline["gray"])
            fresh_rows = _resize_to_grid(
                fresh[0], fresh[1], fresh[2])
        except Exception:  # noqa: BLE001 — malformed frames
            stored_rows = None  # are unusable, not a refusal
            fresh_rows = None
        if stored_rows is None or fresh_rows is None:
            return _unchecked("unusable: a frame could not be "
                              "resized to the compare grid")
        compared = _compare_grids(
            stored_rows, fresh_rows, mean_max=mean_max,
            large_diff=large_diff, frac_large_max=frac_large_max)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if elapsed_ms > budget_ms:
            return _unchecked("slow-grab: check took %.0f ms, over "
                              "the %.0f ms budget" % (
                                  elapsed_ms, budget_ms))
        if compared["match"]:
            return {"verdict": "fresh", "reason": compared["reason"],
                    "mean_abs_diff": compared["mean_abs_diff"],
                    "frac_large": compared["frac_large"],
                    "elapsed_ms": elapsed_ms}
        return {"verdict": "stale", "reason": compared["reason"],
                "mean_abs_diff": compared["mean_abs_diff"],
                "frac_large": compared["frac_large"],
                "elapsed_ms": elapsed_ms}
    except Exception as exc:  # noqa: BLE001 — the check must
        return _unchecked("check-error: %s" % (exc,))  # never break a call
