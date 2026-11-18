"""B-D1 (2026-11-09) PyWinCtl + PyMonCtl window control — the ONE wrapper
every window action goes through.

THE GAP (proved first, file/line evidence)
------------------------------------------
There is NO PyWinCtl / PyMonCtl anywhere in the tree before this
module: zero ``pywinctl`` / ``pymonctl`` imports (grep over core/,
gui/, tests/ — the only hits were the scouting docs), and zero
imports in either interpreter before this batch (``import pywinctl``
fails ImportError on both until the batch installed it; it now
fails on the headless Linux lab with
``Xlib.error.DisplayNameError: Bad display name ""`` — no X
display — which is why the real backend below is lazy and degrades
honestly to ``engine_unavailable``).

What the tree ALREADY had (hand-rolled ctypes, NOT replaced here):
* ``core/skills_hands3_window_manip.py:224`` — ``window_snap``:
  halves + quarters + maximise on the FOREGROUND window, monitor
  aware via ``_monitor_for_window``, read-back verified +/-2 px.
* ``core/skills_hands3_window_manip.py:333`` —
  ``move_window_to_monitor`` (active window between monitors).
* ``core/desktop/control_window.py:72`` — ``window_control``:
  list / minimise / maximise / restore / focus / close + virtual
  desktops, title-resolved, exact-beats-substring,
  refuse-on-ambiguity, focus verified.
* ``core/skills_desktop_tiling.py`` — ``tile_windows`` (up to 12
  windows, primary monitor work area, read-back +/-8 px).
* ``core/skills_hands3_window_focus.py`` — focus helpers.
* ``core/desktop/control_window.py:7`` — ``_enum_windows()``
  (internal list; NO user-facing list tool) and ``:175``
  ``_pick_window`` (exact-beats-substring, refuse-on-ambiguity).

Genuinely missing — what this module adds:
1. No PyWinCtl / PyMonCtl library: ctypes hand-rolled in >= 6
   modules with duplicated RECT / MONITORINFOEX structs.
2. No move/resize of a NAMED window to explicit geometry
   (x, y, w, h).
3. No top/bottom-half or thirds snap (``_SNAP_POSITIONS`` =
   left/right/top-left/top-right/bottom-left/bottom-right/
   maximise only).
4. No handle-based, structured (dict-returning) window surface:
   everything returns prose strings and re-resolves by title
   per call.
5. No single wrapper the coordinator/driver can call.

LICENCE (read from the wheels at build time, 2026-11-09)
--------------------------------------------------------
PyWinCtl 0.4.1 — BSD-3-Clause, "Copyright (c) 2021, Kalmat
<palookjones@gmail.com>", METADATA confirms OSI Approved BSD
License. PyMonCtl 0.92 — BSD-3-Clause, "Copyright (c) 2021,
Kalmat (aka alef)" (its LICENSE.txt names "PyAutoGUI" in the
non-endorsement clause — a copy-paste artifact; the licence is
still BSD-3). Commercial use permitted. Both recorded in
THIRD_PARTY_LICENSES.md.

DEPENDENCY DECISION — PROPOSED, parent rules (same shape as
SymPy in U10)
--------------------
Use the INSTALLED packages as DECLARED dependencies:
``pywinctl>=0.4.1,<0.5`` in requirements.txt, pinned
``pywinctl==0.4.1`` + ``pymonctl==0.92`` in requirements.lock.
REJECTED: vendoring (a second window library that can drift —
same reason U8 rejected vendoring py-fsrs); re-implementing
in-tree (the tree ALREADY has a hand-rolled ctypes
implementation — a third one). The wrapper is ADDITIVE: the
existing ctypes tools are NOT rewritten in this batch.

LOPHOLE FAMILY — "a window command that acts on the wrong window"
---------------------------------------------------------------
* A title match is not identity: two windows share a title and
  the wrong one moves.
* A snap uses the wrong monitor's geometry: a window on the
  second monitor snaps to monitor 0's work area.
* A focus silently fails: activate() is refused by Windows'
  foreground rules and nobody checks.
* A move lands off-screen / is refused (maximised window):
  fire-and-forget reports success anyway.
* No window matches: the command acts anyway or says nothing.

ONE DESIGN (this module, all actions through it)
------------------------------------------------
* Windows are identified by HANDLE, never by title alone: a
  title is a search hint; ``resolve()`` turns it into a handle
  once (exact-beats-substring, refuse-on-ambiguity with the
  candidate list, honest no-match) and every action takes the
  handle.
* Every action reads the window back after acting and compares
  against what was asked (+/-2 px, the tree's existing
  window_snap tolerance): a mismatch is an honest failure, never
  a success claim. A window that disappears between list and
  action fails honestly.
* Monitor geometry comes from PyMonCtl for the monitor the
  window is ON (centre containment, primary fallback — the same
  rule as ``_monitor_for_window``), never monitor 0: a snap
  computes from that monitor's WORK AREA.
* A maximised/minimised window is restored before a move/resize
  (Windows refuses to move a maximised window); the restore is
  itself read back.
* A no-match or failed action returns ``ok: False`` with a
  reason — never a silent no-op, never a success claim.
* The backend is LAZY and INJECTABLE: the real backend imports
  PyWinCtl/PyMonCtl only when first used (the Linux lab has no X
  display, so the import fails there — ``engine_available()``
  is False and every call degrades to ``engine_unavailable``
  instead of crashing); tests inject a fake backend. The
  real-Windows leg (real windows moving on his machine) is a
  field leg, owed, not this batch.

REJECTED ALTERNATIVES
---------------------
* Title-only identity — two windows share a title; the wrong
  one moves. Rejected: handle identity (above).
* Monitor-0 geometry — a window on the second monitor snaps to
  the wrong screen. Rejected: the window's own monitor.
* Fire-and-forget moves — no read-back means a failed move
  reports success. Rejected: read-back after every action.
* Silent no-match — the user asked for a window that isn't
  there. Rejected: honest failure with the reason.
* Rewriting the existing ctypes tools on PyWinCtl — churn with
  no user-visible gain and new regression surface. Rejected:
  additive wrapper; existing tools stay exactly as-is.

The tool handler ``window_place`` (registered in
core/skills_window_control.py, beside window_snap) is the only
new tool: title + either a snap position (12) or explicit
geometry. List/focus already exist as tools — no duplicates.
"""

from __future__ import annotations

import importlib

# Read-back tolerance in pixels — the same +/-2 px the tree's
# existing window_snap uses (window borders lie by a pixel).
_GEOMETRY_TOLERANCE_PX = 2

# The 12 snap positions. Geometry is computed from the window's
# OWN monitor work area (PyMonCtl), never monitor 0. The table is
# the RectangleWin snap table from round 6 track 1, re-implemented
# on PyWinCtl — not vendored.
SNAP_POSITIONS = (
    "left", "right", "top", "bottom",
    "left-third", "center-third", "right-third",
    "top-left", "top-right", "bottom-left", "bottom-right",
    "maximise",
)


class _EngineUnavailable(Exception):
    """Raised by the real backend when PyWinCtl/PyMonCtl cannot be
    imported or initialised (no X display on the headless Linux
    lab; missing install). Carries the underlying error text so
    the caller can report it honestly instead of guessing."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# ----------------------------------------------------------------
# Backend protocol
# ----------------------------------------------------------------
# A backend is any object with:
#   list_windows() -> list of window objects (PyWinCtl Window
#       duck-type: getHandle(), title, left, top, width, height,
#       moveTo(x, y), resizeTo(w, h), minimize(), maximize(),
#       restore(), activate(), isMinimized(), isMaximized(),
#       isActive())
#   active_window() -> window object or None
#   monitors() -> list of monitor objects (PyMonCtl Monitor
#       duck-type: name, isPrimary, box, workarea — box/workarea
#       are Rect-like with .left/.top/.right/.bottom)
# The real backend adapts PyWinCtl + PyMonCtl; tests inject a
# fake. Nothing else in this module knows which backend it has.


class _PyWinCtlBackend:
    """The real backend: PyWinCtl windows + PyMonCtl monitors.
    Imports are lazy (inside the constructor) so importing this
    module never touches the X display / Win32 — the import
    failure is caught and reported as engine_unavailable."""

    def __init__(self):
        try:
            self._pywinctl = importlib.import_module("pywinctl")
            self._pymonctl = importlib.import_module("pymonctl")
        except Exception as e:  # noqa: BLE001 — ANY import failure
            # (ImportError, Xlib DisplayNameError, ...) is an
            # unavailable engine, not a crash.
            raise _EngineUnavailable(
                f"PyWinCtl/PyMonCtl unavailable: {type(e).__name__}: {e}"
            ) from e

    def list_windows(self):
        return list(self._pywinctl.getAllWindows())

    def active_window(self):
        return self._pywinctl.getActiveWindow()

    def monitors(self):
        return list(self._pymonctl.getAllMonitors())


# The real backend is built at most once per process; a failed
# build is remembered too, so a second call does not re-import.
_real_backend = None
_real_backend_error = None


def _get_backend(backend):
    """The backend to use: the injected one, else the lazy real
    one. Raises _EngineUnavailable when the real one cannot be
    built."""
    global _real_backend, _real_backend_error
    if backend is not None:
        return backend
    if _real_backend is not None:
        return _real_backend
    if _real_backend_error is not None:
        raise _EngineUnavailable(_real_backend_error)
    try:
        _real_backend = _PyWinCtlBackend()
    except _EngineUnavailable as e:
        _real_backend_error = e.detail
        raise
    return _real_backend


def engine_available(backend=None) -> bool:
    """True when a backend can be built/used. With an injected
    backend always True; with the real backend True only when
    PyWinCtl + PyMonCtl import cleanly (False on the headless
    Linux lab — no X display — and on a machine without the
    install)."""
    if backend is not None:
        return True
    try:
        _get_backend(None)
    except _EngineUnavailable:
        return False
    return True


def engine_status(backend=None) -> dict:
    """Honest engine report: {"available": bool, "backend":
    "pywinctl+pymonctl" | "injected", "error": str | None}."""
    if backend is not None:
        return {"available": True, "backend": "injected", "error": None}
    try:
        _get_backend(None)
    except _EngineUnavailable as e:
        return {"available": False, "backend": "pywinctl+pymonctl",
                "error": e.detail}
    return {"available": True, "backend": "pywinctl+pymonctl",
            "error": None}


# ----------------------------------------------------------------
# Normalisation helpers (PyWinCtl/PyMonCtl objects -> plain dicts)
# ----------------------------------------------------------------

def _window_info(win, monitors):
    """A window object -> the structured dict the API returns.
    ``monitors`` is the normalised monitor list (for the monitor
    index); None when monitors are unknown."""
    info = {
        "handle": win.getHandle(),
        "title": win.title,
        "left": int(win.left),
        "top": int(win.top),
        "width": int(win.width),
        "height": int(win.height),
        "monitor": _monitor_for_box(
            monitors, int(win.left), int(win.top),
            int(win.width), int(win.height)) if monitors else None,
    }
    return info


def _monitor_dict(mon, index: int) -> dict:
    """A PyMonCtl monitor -> plain dict. ``box`` is the full
    rect; ``workarea`` is the usable rect (taskbar excluded).
    Rects are (left, top, right, bottom)."""
    box = mon.box
    work = mon.workarea
    return {
        "index": index,
        "name": getattr(mon, "name", None),
        "primary": bool(getattr(mon, "isPrimary", index == 0)),
        "left": int(box.left),
        "top": int(box.top),
        "width": int(box.right - box.left),
        "height": int(box.bottom - box.top),
        "work_left": int(work.left),
        "work_top": int(work.top),
        "work_right": int(work.right),
        "work_bottom": int(work.bottom),
    }


def _monitor_for_box(monitors, left, top, width, height):
    """The monitor index a window sits on: the monitor whose
    rect contains the window's CENTRE; primary fallback; None
    when there are no monitors. Same rule as the tree's
    ``_monitor_for_window`` — never monitor 0 by default."""
    if not monitors:
        return None
    cx = left + width // 2
    cy = top + height // 2
    for m in monitors:
        if (m["left"] <= cx < m["left"] + m["width"]
                and m["top"] <= cy < m["top"] + m["height"]):
            return m["index"]
    for m in monitors:
        if m["primary"]:
            return m["index"]
    return monitors[0]["index"]


def _list_monitors(backend) -> list:
    """Normalised monitor list, or [] when monitors cannot be
    read (a window list without monitor assignment is still
    useful; the caller records monitor None)."""
    try:
        return [_monitor_dict(m, i)
                for i, m in enumerate(backend.monitors())]
    except Exception:  # noqa: BLE001 — monitor read is best-effort
        return []


def _find_window(backend, handle):
    """The live window object with this handle, or None when it
    no longer exists (disappeared between list and action)."""
    for win in backend.list_windows():
        if win.getHandle() == handle:
            return win
    return None


def _close_enough(a: dict, b: dict) -> bool:
    """Geometry match within the read-back tolerance."""
    return (abs(a["left"] - b["left"]) <= _GEOMETRY_TOLERANCE_PX
            and abs(a["top"] - b["top"]) <= _GEOMETRY_TOLERANCE_PX
            and abs(a["width"] - b["width"]) <= _GEOMETRY_TOLERANCE_PX
            and abs(a["height"] - b["height"])
            <= _GEOMETRY_TOLERANCE_PX)


def _geom_str(g: dict) -> str:
    return (f"({g['left']}, {g['top']}, {g['width']}x{g['height']})")


def _fail(reason: str, **extra) -> dict:
    out = {"ok": False, "reason": reason}
    out.update(extra)
    return out


# ----------------------------------------------------------------
# Public structured API
# ----------------------------------------------------------------

def list_windows(backend=None) -> dict:
    """All windows as structured dicts: {"ok", "windows":
    [{handle, title, left, top, width, height, monitor}],
    "reason"}. ``monitor`` is the index of the monitor the
    window sits on (None when monitors cannot be read)."""
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", windows=[])
    try:
        monitors = _list_monitors(backend)
        windows = [_window_info(w, monitors)
                   for w in backend.list_windows()]
    except Exception as e:  # noqa: BLE001 — a backend that dies
        # mid-list is an honest failure, not a partial list.
        return _fail(f"list_failed: {type(e).__name__}: {e}",
                     windows=[])
    return {"ok": True, "windows": windows, "reason": None}


def monitors(backend=None) -> dict:
    """PyMonCtl monitors: {"ok", "monitors": [{index, name,
    primary, left, top, width, height, work_left, work_top,
    work_right, work_bottom}], "reason"}."""
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", monitors=[])
    try:
        listed = _list_monitors(backend)
    except Exception as e:  # noqa: BLE001
        return _fail(f"monitors_failed: {type(e).__name__}: {e}",
                     monitors=[])
    if not listed:
        return _fail("monitors_failed: no monitors reported",
                     monitors=[])
    return {"ok": True, "monitors": listed, "reason": None}


def resolve(title: str, backend=None) -> dict:
    """A title (search hint) -> a handle (identity): {"ok",
    "handle", "title", "candidates", "reason"}. Exact match
    beats substring (the tree's ``_pick_window`` discipline);
    no match is an honest failure; more than one match refuses
    with the candidate list — it NEVER picks one for the caller
    and moves the wrong window."""
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", handle=None,
                     title=None, candidates=[])
    listed = list_windows(backend=backend)
    if not listed["ok"]:
        return _fail(listed["reason"], handle=None, title=None,
                     candidates=[])
    query = (title or "").strip()
    if not query:
        return _fail("no_match: empty title", handle=None,
                     title=None, candidates=[])
    exact = [w for w in listed["windows"] if w["title"] == query]
    if len(exact) == 1:
        return {"ok": True, "handle": exact[0]["handle"],
                "title": exact[0]["title"], "candidates": [],
                "reason": None}
    if len(exact) > 1:
        return _fail(
            f"ambiguous: {len(exact)} windows titled {query!r} — "
            "say which one (e.g. by its app or position)",
            handle=None, title=None,
            candidates=[{"handle": w["handle"], "title": w["title"]}
                        for w in exact])
    lowered = query.lower()
    sub = [w for w in listed["windows"]
           if lowered in (w["title"] or "").lower()]
    if len(sub) == 1:
        return {"ok": True, "handle": sub[0]["handle"],
                "title": sub[0]["title"], "candidates": [],
                "reason": None}
    if len(sub) > 1:
        return _fail(
            f"ambiguous: {len(sub)} windows match {query!r} — "
            "be more specific",
            handle=None, title=None,
            candidates=[{"handle": w["handle"], "title": w["title"]}
                        for w in sub])
    return _fail(f"no_match: no window titled {query!r}",
                 handle=None, title=None, candidates=[])


def _read_back(backend, handle):
    """The window's current geometry as a dict, or None when it
    no longer exists. Every action ends here — no action claims
    success without this."""
    win = _find_window(backend, handle)
    if win is None:
        return None
    monitors = _list_monitors(backend)
    return _window_info(win, monitors)


def _ensure_movable(backend, handle):
    """Restore a maximised/minimised window before a move/resize
    — Windows refuses to move a maximised window. Returns
    (True, None) or (False, reason). The restore is itself
    read back."""
    win = _find_window(backend, handle)
    if win is None:
        return False, "window_disappeared: the window no longer exists"
    try:
        if win.isMaximized() or win.isMinimized():
            win.restore()
            after = _read_back(backend, handle)
            if after is None:
                return False, ("window_disappeared: the window no "
                               "longer exists")
            if _find_window(backend, handle).isMaximized():
                return False, ("restore_failed: the window is still "
                               "maximised, so it cannot be moved")
    except Exception as e:  # noqa: BLE001
        return False, f"restore_failed: {type(e).__name__}: {e}"
    return True, None


def move(handle, left: int, top: int, width: int | None = None,
         height: int | None = None, backend=None) -> dict:
    """Move a window by handle (and resize it when width/height
    are given). Returns {"ok", "geometry" (the READ-BACK actual
    geometry), "reason"}. Success requires the read-back to
    match what was asked within +/-2 px."""
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", geometry=None)
    ok, reason = _ensure_movable(backend, handle)
    if not ok:
        return _fail(reason, geometry=None)
    win = _find_window(backend, handle)
    if win is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None)
    asked = {"left": int(left), "top": int(top)}
    try:
        win.moveTo(int(left), int(top))
        if width is not None and height is not None:
            win.resizeTo(int(width), int(height))
            asked["width"] = int(width)
            asked["height"] = int(height)
    except Exception as e:  # noqa: BLE001
        return _fail(f"move_failed: {type(e).__name__}: {e}",
                     geometry=None)
    actual = _read_back(backend, handle)
    if actual is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None)
    for key in ("left", "top", "width", "height"):
        asked.setdefault(key, actual[key])
    if not _close_enough(asked, actual):
        return _fail(
            f"move_did_not_land: asked {_geom_str(asked)}, "
            f"read back {_geom_str(actual)}",
            geometry=actual)
    return {"ok": True, "geometry": actual, "reason": None}


def resize(handle, width: int, height: int, backend=None) -> dict:
    """Resize a window by handle. Same read-back contract as
    move()."""
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", geometry=None)
    ok, reason = _ensure_movable(backend, handle)
    if not ok:
        return _fail(reason, geometry=None)
    win = _find_window(backend, handle)
    if win is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None)
    asked = {"left": int(win.left), "top": int(win.top),
             "width": int(width), "height": int(height)}
    try:
        win.resizeTo(int(width), int(height))
    except Exception as e:  # noqa: BLE001
        return _fail(f"resize_failed: {type(e).__name__}: {e}",
                     geometry=None)
    actual = _read_back(backend, handle)
    if actual is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None)
    if not _close_enough(asked, actual):
        return _fail(
            f"resize_did_not_land: asked {_geom_str(asked)}, "
            f"read back {_geom_str(actual)}",
            geometry=actual)
    return {"ok": True, "geometry": actual, "reason": None}


def focus(handle, backend=None) -> dict:
    """Focus a window by handle: activate(), then read back the
    ACTIVE window — success requires the active handle to be
    this handle. A refused activation (Windows foreground
    rules) is an honest failure, never a silent one."""
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", geometry=None)
    win = _find_window(backend, handle)
    if win is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None)
    try:
        win.activate()
    except Exception as e:  # noqa: BLE001
        return _fail(f"focus_failed: {type(e).__name__}: {e}",
                     geometry=None)
    try:
        active = backend.active_window()
    except Exception as e:  # noqa: BLE001
        return _fail(f"focus_unverified: {type(e).__name__}: {e}",
                     geometry=None)
    actual = _read_back(backend, handle)
    if actual is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None)
    if active is None or active.getHandle() != handle:
        return _fail(
            "focus_failed: the window did not become active "
            "(Windows refused the activation)",
            geometry=actual)
    return {"ok": True, "geometry": actual, "reason": None}


def _set_state(handle, action: str, backend=None) -> dict:
    """minimise / maximise / restore by handle, with the state
    read back. ``action`` is one of "minimise", "maximise",
    "restore"."""
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", geometry=None,
                     state=None)
    win = _find_window(backend, handle)
    if win is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None, state=None)
    try:
        {"minimise": win.minimize,
         "maximise": win.maximize,
         "restore": win.restore}[action]()
    except Exception as e:  # noqa: BLE001
        return _fail(f"{action}_failed: {type(e).__name__}: {e}",
                     geometry=None, state=None)
    actual = _read_back(backend, handle)
    if actual is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None, state=None)
    win = _find_window(backend, handle)
    state = ("minimised" if win.isMinimized()
             else "maximised" if win.isMaximized() else "normal")
    want = {"minimise": "minimised", "maximise": "maximised",
            "restore": "normal"}[action]
    if state != want:
        return _fail(
            f"{action}_did_not_land: window is {state}",
            geometry=actual, state=state)
    return {"ok": True, "geometry": actual, "reason": None,
            "state": state}


def minimise(handle, backend=None) -> dict:
    """Minimise a window by handle (state read back)."""
    return _set_state(handle, "minimise", backend=backend)


def maximise(handle, backend=None) -> dict:
    """Maximise a window by handle (state read back)."""
    return _set_state(handle, "maximise", backend=backend)


def restore(handle, backend=None) -> dict:
    """Restore a window by handle (state read back)."""
    return _set_state(handle, "restore", backend=backend)


def _snap_rect(position: str, work: dict) -> dict:
    """The geometry for ``position`` inside a monitor's work
    area (work_* keys from _monitor_dict). Thirds use the
    remainder for the last third so the three sum exactly."""
    wl, wt = work["work_left"], work["work_top"]
    ww = work["work_right"] - work["work_left"]
    wh = work["work_bottom"] - work["work_top"]
    half_w, half_h = ww // 2, wh // 2
    third_w = ww // 3
    table = {
        "left": (wl, wt, half_w, wh),
        "right": (wl + half_w, wt, ww - half_w, wh),
        "top": (wl, wt, ww, half_h),
        "bottom": (wl, wt + half_h, ww, wh - half_h),
        "left-third": (wl, wt, third_w, wh),
        "center-third": (wl + third_w, wt, third_w, wh),
        "right-third": (wl + 2 * third_w, wt, ww - 2 * third_w, wh),
        "top-left": (wl, wt, half_w, half_h),
        "top-right": (wl + half_w, wt, half_w, half_h),
        "bottom-left": (wl, wt + half_h, half_w, wh - half_h),
        "bottom-right": (wl + half_w, wt + half_h, half_w,
                         wh - half_h),
    }
    left, top, width, height = table[position]
    return {"left": left, "top": top, "width": width, "height": height}


def snap(handle, position: str, backend=None) -> dict:
    """Snap a window by handle to ``position`` (one of
    SNAP_POSITIONS) inside ITS OWN monitor's work area
    (PyMonCtl — never monitor 0). "maximise" maximises.
    Returns {"ok", "geometry" (read-back), "position",
    "monitor", "reason"}. The read-back must match the
    computed rect within +/-2 px (or the maximised state)."""
    if position not in SNAP_POSITIONS:
        return _fail(
            f"bad_position: {position!r} is not one of "
            f"{', '.join(SNAP_POSITIONS)}",
            geometry=None, position=position, monitor=None)
    try:
        backend = _get_backend(backend)
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}", geometry=None,
                     position=position, monitor=None)
    win = _find_window(backend, handle)
    if win is None:
        return _fail("window_disappeared: the window no longer exists",
                     geometry=None, position=position, monitor=None)
    monitors = _list_monitors(backend)
    if not monitors:
        return _fail("monitors_failed: no monitors reported",
                     geometry=None, position=position, monitor=None)
    current = _window_info(win, monitors)
    monitor = next((m for m in monitors
                    if m["index"] == current["monitor"]),
                   monitors[0])
    if position == "maximise":
        done = maximise(handle, backend=backend)
        return {**done, "position": position,
                "monitor": monitor["index"]}
    target = _snap_rect(position, monitor)
    moved = move(handle, target["left"], target["top"],
                 target["width"], target["height"], backend=backend)
    if not moved["ok"]:
        return {**moved, "position": position,
                "monitor": monitor["index"]}
    return {"ok": True, "geometry": moved["geometry"],
            "position": position, "monitor": monitor["index"],
            "reason": None}


# ----------------------------------------------------------------
# Tool handler: window_place (registered in
# core/skills_window_control.py, beside window_snap)
# ----------------------------------------------------------------

def window_place(title: str, position: str | None = None,
                 left: int | None = None, top: int | None = None,
                 width: int | None = None,
                 height: int | None = None,
                 backend=None) -> str:
    """Place a NAMED window: either snap it to ``position`` (one
    of the 12 SNAP_POSITIONS) or move/resize it to explicit
    geometry. The title is resolved ONCE to a handle (exact-beats-
    substring, refuse-on-ambiguity); the action reads the geometry
    back and the reply reports the ACTUAL geometry. A failure is
    an honest sentence, never a silent no-op. ``backend`` is the
    injectable test seam (None = the real PyWinCtl backend)."""
    if position is not None and position not in SNAP_POSITIONS:
        return (f"Could not place {title!r}: {position!r} is not a "
                f"snap position — use one of "
                f"{', '.join(SNAP_POSITIONS)}.")
    has_geometry = (left is not None or top is not None
                    or width is not None or height is not None)
    if position is None and not has_geometry:
        return (f"Could not place {title!r}: give a snap position "
                "or a geometry (left/top and/or width/height).")
    if position is not None and has_geometry:
        return (f"Could not place {title!r}: give either a snap "
                "position or explicit geometry, not both.")
    if has_geometry and ((left is None) != (top is None)
                         or (width is None) != (height is None)):
        return (f"Could not place {title!r}: a move needs both "
                "left and top, a resize needs both width and "
                "height.")
    resolved = resolve(title, backend=backend)
    if not resolved["ok"]:
        return f"Could not place {title!r}: {resolved['reason']}."
    handle = resolved["handle"]
    name = resolved["title"]
    if position is not None:
        result = snap(handle, position, backend=backend)
        if not result["ok"]:
            return (f"Could not place {name!r}: "
                    f"{result['reason']}.")
        g = result["geometry"]
        return (f"Snapped {name!r} to {position} on monitor "
                f"{result['monitor']}: now at {_geom_str(g)}.")
    # resolve() already proved the engine is up, so _get_backend
    # here returns the cached real backend (or the injected one)
    # — it cannot raise _EngineUnavailable at this point.
    live = _get_backend(backend)
    current = _read_back(live, handle)
    if current is None:
        return (f"Could not place {name!r}: window_disappeared: "
                "the window no longer exists.")
    result = move(handle,
                  left if left is not None else current["left"],
                  top if top is not None else current["top"],
                  width, height, backend=backend)
    if not result["ok"]:
        return f"Could not place {name!r}: {result['reason']}."
    g = result["geometry"]
    return f"Moved {name!r} to {_geom_str(g)}."
