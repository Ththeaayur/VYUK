"""Wave 5 (v5.60) — PF-17 vision exclusion list, checked BEFORE capture.

Acceptance (PF-17): listed windows/apps/regions are NEVER
captured — not merely blurred afterwards. The capture path checks
this list before grabbing any pixels, proven by test.

The list lives in data/vision_exclusions.json (runtime state, not
shipped content — the shipped data/ dir stays empty):
  {"processes": ["slack"], "title_keywords": ["payroll"],
   "regions": [[0, 0, 400, 300]]}
- processes: matched lowercased, ".exe" stripped, against the
  foreground process name.
- title_keywords: matched lowercased as substrings of the
  foreground window title.
- regions: [x, y, w, h] screen rectangles. A capture whose crop
  rectangle intersects a listed region is refused outright —
  fail-closed, because capturing-then-blacking-out would still
  capture the region first, which PF-17 forbids.

A missing or corrupt file means an EMPTY list (never a crash,
never a block-all): "unknown list" is never treated as "excluded
everything". The built-in denylist in core/capture_guard.py still
applies first and independently; this list EXTENDS it with the
user's own entries.
"""

from __future__ import annotations

import json
import os

FILENAME = "vision_exclusions.json"


def _list_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    tree_root = os.path.dirname(here)
    return os.path.join(tree_root, "data", FILENAME)


def load_exclusions(path: str | None = None) -> dict:
    """Load the user exclusion list; missing/corrupt = empty."""
    target = path or _list_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {"processes": [], "title_keywords": [], "regions": [],
                "corrupt_or_missing": True}
    if not isinstance(raw, dict):
        return {"processes": [], "title_keywords": [], "regions": [],
                "corrupt_or_missing": True}
    processes = raw.get("processes", [])
    keywords = raw.get("title_keywords", [])
    regions = raw.get("regions", [])
    return {
        "processes": [str(p).lower().removesuffix(".exe")
                      for p in processes if isinstance(p, str)],
        "title_keywords": [str(k).lower()
                           for k in keywords if isinstance(k, str)],
        "regions": [tuple(int(v) for v in r)
                    for r in regions
                    if isinstance(r, (list, tuple)) and len(r) == 4],
        "corrupt_or_missing": False,
    }


def _rects_intersect(crop_ltrb: tuple, region_xywh: tuple) -> bool:
    """Crop (left, top, right, bottom) vs region (x, y, w, h)."""
    cl, ct, cr, cb = crop_ltrb
    rx, ry, rw, rh = region_xywh
    return (cl < rx + rw and rx < cr
            and ct < ry + rh and ry < cb)


def user_exclusion_block(process: str | None, title: str | None,
                         crop_ltrb: tuple | None,
                         exclusions: dict | None = None) -> str | None:
    """Reason string when the user list excludes this capture.

    crop_ltrb is (left, top, right, bottom) in screen coordinates
    — the same shape foreground_window_rect() returns. Returns
    None when nothing matches. Checked BEFORE any grab: a match
    means the capture never happens, not that its output is
    redacted afterwards.
    """
    rules = exclusions if exclusions is not None else load_exclusions()
    proc = (process or "").lower().removesuffix(".exe")
    if proc and proc in rules["processes"]:
        return ("Vision exclusion list: process %r is listed "
                "and is never captured." % proc)
    lowered_title = (title or "").lower()
    for keyword in rules["title_keywords"]:
        if keyword and keyword in lowered_title:
            return ("Vision exclusion list: window title matches "
                    "listed keyword %r and is never captured."
                    % keyword)
    if crop_ltrb is not None:
        crop = tuple(int(v) for v in crop_ltrb)
        for region in rules["regions"]:
            if _rects_intersect(crop, region):
                return ("Vision exclusion list: capture region "
                        "intersects listed region %r and is never "
                        "captured." % (region,))
    return None


def foreground_probe() -> tuple:
    """(process, title) of the foreground window, best effort.

    Composes the same two seams core/capture_guard.py trusts.
    Unknown foreground yields (None, None) and blocks nothing —
    mirroring the guard's posture.
    """
    process = None
    title = None
    try:
        from .input_guard import _foreground_process_name
        process = _foreground_process_name()
    except Exception:  # noqa: BLE001 — probe failure is not a block
        pass
    try:
        from .desktop.uia import foreground_title  # Phase 3 D4b: uia hub moved to core/desktop/
        title = foreground_title()
    except Exception:  # noqa: BLE001 — probe failure is not a block
        pass
    return process, title
