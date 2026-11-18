"""Unified perception adapter — §7 item 2 (parent draft, existing sources only).

One observation, one stamp: a single :func:`observe` call returns one
snapshot whose sections — grounding marks, control enumeration, OCR
words, pixel probes, window/foreground identity — all describe the SAME
capture, and every consumer of that snapshot reads the same stamp.
Consumers never mix sections from different stamps: a snapshot is
either whole or it is refused as stale (see :func:`is_stale`).

Snapshot shape::

    {stamp, stamped_at, scope, foreground_identity, bounds,
     marks, controls, ocr_words, pixels, notes,
     capture_status, capture_reason}

* ``stamp`` is minted once per observation from the foreground hwnd +
  title + capture bounds + a per-process observation counter, so two
  consumers of one snapshot share it and a fresh observation always
  mints a fresh stamp — even of an unchanged screen. The stamp
  identifies the capture event; staleness is judged from the
  foreground identity + bounds it was minted under, never from
  stamp equality alone.
* ``capture_status`` is ``ok``, ``denied`` (capture denylist),
  ``unavailable`` (a section's source cannot run here) or ``failed``
  (the capture itself raised). ``capture_reason`` carries the honest
  reason; a failed/denied snapshot is EMPTY — never a recycled
  snapshot, never a partial snapshot presented as whole.
* ``notes`` maps section name -> "" or the honest reason that
  section degraded. Sections degrade independently: if OCR fails,
  the snapshot still returns with ``ocr_words: []`` and a note —
  but marks/controls never silently inherit another snapshot's stamp.

Honest scope note (read before assuming more than this module does):
the tree has NO image-driven UIA walk — marks/controls come from the
existing UIA descendant walk against the SAME foreground window
during the same observation, while OCR/pixels run against the single
captured image. The adapter guarantees one stamp + one foreground +
one image per observation; it does NOT pretend the UIA walk reads
pixels. Building an image-driven walk would be a new perception
engine and is G1-gated (below).

The adapter is read-only and side-effect-free by construction: it
dispatches no tool, touches no funnel or gate stack, and never
carries credentials, password-field content, or secure-desktop
content (by-design walls: UAC secure desktop, browser password
fields, CAPTCHAs are never bypassed — the capture denylist is
enforced before any section runs).

Handles (item 1): consumers resolve targets through
``core.desktop.target_handles``, never around it. This module only supplies
observation evidence: :func:`snapshot_window_identity` extracts the
``{hwnd, pid, title}`` a caller mints a window handle from, and
:func:`is_stale` mirrors the item-1 mark-staleness rule (a foreground
hwnd change refuses even when the title matches; title is the
fallback only when hwnd is unknown on either side; an unreadable
title never counts as a change).

GATED — what this adapter deliberately does NOT contain (licence
gate G1, NOT cleared by this build):

* No external perception engine, no R3-G-class engine, no unnamed
  offline model. The adapter ships with existing sources only
  (marks/controls/OCR/pixels already in the tree).
* No image-driven UIA walk, no model-derived grounding. A future
  engine would arrive as a new backend implementing the same
  section protocol — wiring one needs its engine named first and
  its licence read first. Nothing here clears that.
* No continuous/periodic observation (owner's open D2 question).
  Observation is on-demand per action; continuous capture stays
  OFF by default, opt-in only.

Backends: :func:`observe` takes an optional ``backend`` implementing
the section protocol (``foreground_identity``, ``bounds``,
``capture_block_reason``, ``capture``, ``marks``, ``controls``,
``ocr_words``, ``pixels``). The default backend wires the tree's
existing sources with lazy imports, so importing this module needs
only the standard library. Tests drive the protocol with a fake
backend; the live app uses the default backend.
"""

import hashlib
import itertools
import threading
from datetime import datetime, timezone

__all__ = [
    "observe",
    "is_stale",
    "snapshot_window_identity",
    "describe",
    "default_backend",
    "validate_marks",
    "EMPTY_SNAPSHOT_SECTIONS",
]

#: Sections that always exist on a snapshot, even when empty.
EMPTY_SNAPSHOT_SECTIONS = ("marks", "controls", "ocr_words", "pixels")

_counter = itertools.count(1)
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Stamp + snapshot construction
# ---------------------------------------------------------------------------

def _mint_stamp(foreground_identity, bounds):
    """One stamp per observation, from hwnd + title + bounds + counter."""
    with _lock:
        seq = next(_counter)
    hwnd = (foreground_identity or {}).get("hwnd")
    title = (foreground_identity or {}).get("title")
    raw = "%s|%s|%s|%s" % (hwnd, title, bounds, seq)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _empty_snapshot(foreground_identity, bounds, scope, status, reason):
    return {
        "stamp": _mint_stamp(foreground_identity, bounds),
        "stamped_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "foreground_identity": dict(foreground_identity or {}),
        "bounds": bounds,
        "marks": [],
        "controls": [],
        "ocr_words": [],
        "pixels": {},
        "notes": {name: reason for name in EMPTY_SNAPSHOT_SECTIONS},
        "capture_status": status,
        "capture_reason": reason,
    }


def _run_section(backend, name, *args):
    """Run one backend section; failure degrades that section only."""
    try:
        result = getattr(backend, name)(*args)
    except Exception as exc:  # noqa: BLE001 — a section never breaks observe
        return [], "section '%s' failed: %s" % (name, exc)
    if isinstance(result, tuple) and len(result) == 2:
        items, note = result
        return items, (note or "")
    return result, ""


# ---------------------------------------------------------------------------
# Staleness — mirrors the item-1 mark rule (core.desktop.uia._marks_stale_reason)
# ---------------------------------------------------------------------------

def is_stale(snapshot, current=None, *, backend=None):
    """Return None when ``snapshot`` is still fresh, else the reason.

    ``current`` is ``(foreground_identity, bounds)``; when omitted it
    is read from ``backend`` (default backend when None). Rules, in
    order: a non-``ok`` snapshot is stale (it was never a successful
    capture); a foreground hwnd change refuses even when the title
    matches; when hwnd is unknown on either side the title is the
    fallback (a title change refuses, an unreadable/None title never
    counts as a change); a bounds change refuses. A consumer holding
    a stale snapshot must re-observe — never act on it.
    """
    if not isinstance(snapshot, dict):
        return "not a snapshot: re-observe before acting"
    if snapshot.get("capture_status") not in (None, "ok"):
        return ("snapshot was not a successful capture (%s: %s) — "
                "re-observe before acting"
                % (snapshot.get("capture_status"),
                   snapshot.get("capture_reason") or "no reason given"))
    old_fg = snapshot.get("foreground_identity") or {}
    old_bounds = snapshot.get("bounds")
    if current is None:
        be = backend if backend is not None else default_backend()
        try:
            cur_fg = be.foreground_identity()
        except Exception:  # noqa: BLE001
            cur_fg = {}
        try:
            cur_bounds = be.bounds()
        except Exception:  # noqa: BLE001
            cur_bounds = None
    else:
        cur_fg, cur_bounds = current
        cur_fg = cur_fg or {}
    old_hwnd = old_fg.get("hwnd")
    cur_hwnd = cur_fg.get("hwnd")
    if old_hwnd is not None and cur_hwnd is not None:
        if old_hwnd != cur_hwnd:
            return ("foreground window changed (hwnd %s -> %s) even "
                    "though the title may match — re-observe before "
                    "acting" % (old_hwnd, cur_hwnd))
    else:
        old_title = old_fg.get("title")
        cur_title = cur_fg.get("title")
        if old_title is not None and cur_title is not None:
            if old_title != cur_title:
                return ("foreground title changed ('%s' -> '%s') and "
                        "no window handle proves sameness — re-observe "
                        "before acting" % (old_title, cur_title))
        # An unreadable title on either side never counts as a change.
    if (old_bounds is not None and cur_bounds is not None
            and tuple(old_bounds) != tuple(cur_bounds)):
        return ("capture bounds changed (%s -> %s) — re-observe "
                "before acting" % (old_bounds, cur_bounds))
    return None


def snapshot_window_identity(snapshot):
    """``{hwnd, pid, title}`` from a snapshot, for item-1 minting.

    Only non-None fields are carried (a field the observation could
    not prove is not invented). The caller mints via
    ``core.desktop.target_handles.mint_window``; minting an identity with no
    positive field refuses there, not here.
    """
    fg = (snapshot or {}).get("foreground_identity") or {}
    identity = {}
    for field in ("hwnd", "pid", "title"):
        if fg.get(field) is not None:
            identity[field] = fg[field]
    return identity


def validate_marks(snapshot, monitor_backend=None):
    """Validate a snapshot's marks against a FRESH monitor table
    (consumers batch 10 — the mark-coordinate consumer the item-6
    engine named as its follow-up).

    Delegates to :func:`core.cursor_monitor.validate_marks` and
    returns its shape: {"valid": [...], "flagged": [...],
    "table_note": ""}. Valid marks are copies annotated with
    ``monitor_id``; flagged entries name the mark and the reason
    (no usable rectangle, centre on no monitor, unreadable
    scale). The snapshot is never mutated, and validation never
    invents coordinates. A snapshot's staleness
    (:func:`is_stale`) is a SEPARATE check that dispatch still
    runs first — this annotates/flags by monitor only, against
    the current table, not the snapshot's capture bounds (a
    mark's rect is true pixels, so the fresh table is the right
    oracle). An unreadable table yields unvalidated marks, not
    refused ones (see cursor_monitor's contract).
    """
    from . import cursor_monitor as _cm  # Phase 3 S7a-2: cursor_monitor moved into core/desktop/ (reverts S7a-1 classification fix)

    marks = (snapshot or {}).get("marks") or []
    return _cm.validate_marks(marks, backend=monitor_backend)


def describe(snapshot):
    """One-line human summary, including the stamp (receipts/GUI)."""
    if not isinstance(snapshot, dict):
        return "no snapshot"
    stamp = str(snapshot.get("stamp") or "?")[:12]
    fg = snapshot.get("foreground_identity") or {}
    title = fg.get("title") or "unknown window"
    status = snapshot.get("capture_status") or "ok"
    line = ("snapshot %s — %s (%d marks, %d controls, %d OCR words, "
            "status %s)"
            % (stamp, title, len(snapshot.get("marks") or []),
               len(snapshot.get("controls") or []),
               len(snapshot.get("ocr_words") or []), status))
    reason = snapshot.get("capture_reason")
    if status != "ok" and reason:
        line += " — %s" % reason
    return line


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------

def observe(scope="", *, backend=None):
    """One observation, one stamp: ``Snapshot`` dict (see module doc).

    ``scope`` is a window-title substring ("" = foreground window).
    The backend's ``capture()`` is called EXACTLY ONCE; every section
    runs against that capture / the same foreground window. Capture
    failure (``capture()`` raising) yields an empty snapshot with
    ``capture_status`` ``failed``; a denylisted foreground yields an
    empty snapshot with ``denied`` and NO section runs; a missing
    image (``capture()`` returns None) degrades only the
    image-dependent sections (OCR/pixels) while marks/controls still
    run against the same foreground window. Never raises on a
    section failure — that section degrades with a note.
    """
    be = backend if backend is not None else default_backend()
    try:
        fg = be.foreground_identity() or {}
    except Exception:  # noqa: BLE001
        fg = {}
    try:
        bounds = be.bounds()
    except Exception:  # noqa: BLE001
        bounds = None
    try:
        block = be.capture_block_reason()
    except Exception:  # noqa: BLE001
        block = None
    if block:
        return _empty_snapshot(fg, bounds, scope, "denied", block)
    try:
        capture = be.capture()
    except Exception as exc:  # noqa: BLE001 — capture failure: empty, honest
        return _empty_snapshot(fg, bounds, scope, "failed",
                               "capture failed: %s" % exc)
    notes = {}
    # OCR first: its words feed the marks walk (same capture).
    ocr_words, notes["ocr_words"] = _run_section(be, "ocr_words", capture)
    if capture is None and not notes["ocr_words"]:
        notes["ocr_words"] = "no captured image — OCR unavailable"
    marks, notes["marks"] = _run_section(be, "marks", capture, fg,
                                         ocr_words)
    controls, notes["controls"] = _run_section(be, "controls", capture,
                                               fg)
    pixels, notes["pixels"] = _run_section(be, "pixels", capture)
    if capture is None and not notes["pixels"]:
        notes["pixels"] = "no captured image — pixel probes unavailable"
    return {
        "stamp": _mint_stamp(fg, bounds),
        "stamped_at": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "foreground_identity": dict(fg),
        "bounds": bounds,
        "marks": marks if isinstance(marks, list) else [],
        "controls": controls if isinstance(controls, list) else [],
        "ocr_words": ocr_words if isinstance(ocr_words, list) else [],
        "pixels": pixels if isinstance(pixels, dict) else {},
        "notes": notes,
        "capture_status": "ok",
        "capture_reason": "",
    }


# ---------------------------------------------------------------------------
# Default backend — existing tree sources only, lazy imports
# ---------------------------------------------------------------------------

class _DefaultBackend:
    """Wires the tree's existing sources; no new engine, no new dep.

    Marks/controls come from the existing UIA walk against the same
    foreground window (the tree has no image-driven UIA walk — see
    the module's honest-scope note); OCR/pixels run against the
    single captured image. Every method never raises: an
    unobservable source degrades with a note via _run_section.
    """

    def foreground_identity(self):
        try:
            from . import control as _control  # Phase 3 D4a: control hub now in core/desktop/; S7a interim closed
            return dict(_control._foreground_identity())
        except Exception:  # noqa: BLE001
            return {"hwnd": None, "pid": None, "title": None,
                    "proc": None}

    def bounds(self):
        try:
            from . import vision as _vision  # Phase 3 D4c: vision now in core/desktop/; S7a interim closed
            return _vision.foreground_window_rect()
        except Exception:  # noqa: BLE001
            return None

    def capture_block_reason(self):
        try:
            from .. import capture_guard as _guard  # Phase 3 S7a: capture_guard stays flat in core/
            return _guard.foreground_capture_block()
        except Exception:  # noqa: BLE001
            return None

    def capture(self):
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
            return _uia._screen_image()
        except Exception:  # noqa: BLE001
            return None

    def ocr_words(self, capture):
        if capture is None:
            return [], "no captured image — OCR unavailable"
        try:
            from . import vision as _vision  # Phase 3 D4c: vision now in core/desktop/; S7a interim closed
            words = _vision._ocr_words_for_image(capture, {})
        except Exception:  # noqa: BLE001
            return [], "OCR failed on this capture"
        if words is None:
            return [], "OCR unavailable here (Tesseract not configured)"
        return list(words), ""

    def marks(self, capture, fg, ocr_words):
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
            title = (fg or {}).get("title") or ""
            marks, note = _uia.enumerate_marks(
                window_title=title, ocr_words=ocr_words or None)
            return list(marks), (note or "")
        except Exception as exc:  # noqa: BLE001
            return [], "mark enumeration failed: %s" % exc

    def controls(self, capture, fg):
        try:
            from . import uia as _uia  # Phase 3 D4b: uia hub now in core/desktop/; S7a interim closed
            title = (fg or {}).get("title") or ""
            snap = _uia.control_list_snapshot(window_title=title)
            return list(snap.get("controls") or []), \
                (snap.get("note") or "")
        except Exception as exc:  # noqa: BLE001
            return [], "control enumeration failed: %s" % exc

    def pixels(self, capture):
        if capture is None:
            return {}, "no captured image — pixel probes unavailable"
        try:
            img = capture.convert("RGB")
            width, height = img.size
            points = {
                "centre": (width // 2, height // 2),
                "top_left": (0, 0),
                "top_right": (width - 1, 0),
                "bottom_left": (0, height - 1),
                "bottom_right": (width - 1, height - 1),
            }
            return {name: img.getpixel(xy) for name, xy in
                    points.items()}, ""
        except Exception as exc:  # noqa: BLE001
            return {}, "pixel probes failed: %s" % exc


def default_backend():
    """A fresh default backend (stateless; safe to construct per call)."""
    return _DefaultBackend()
