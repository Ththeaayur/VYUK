"""B-D5 (2026-11-09) screen capture — the ONE dict-returning
wrapper a driver calls for screen frames, over DXcam (Desktop
Duplication, 240 Hz+).

GAPS-19 (2026-11-13, review flaws D-7 + D-8, §6.6): this module is
now the ONE capture owner — every frame in the product comes
through it. The vision path (capture_native_image), the skills
capture tools, the hands scroll/diff seam, the social-send guard
read-back and the display-size probes all delegate here (see
OWNER below). B-D5's "does NOT rewire capture_native_image"
boundary is superseded by GAPS-19: the rewire IS this batch.

THE GAP IT CLOSED (B-D5, proved first, file/line evidence)
----------------------------------------------------------
DXcam existed in the tree ONLY as a probe — nothing ever grabbed
a frame through it:

* ``core/capture.py:42`` ``dxcam_available()`` is an import
  probe (Windows + find_spec). ``capture_bakeoff_status()``
  (:74) has no winner ("DXcam vs windows-capture winner TBD
  by live trials"), and ``occluded_window_capture()`` returns
  "engine-unavailable ... no WGC engine is wired on this
  host". The dxgi-fallback rung had NO engine behind it.
* The only real capture was PIL ``ImageGrab.grab`` via
  ``core/desktop/vision_capture.py:231`` ``_grab_image`` —
  a slow GDI grab, all-screens or bbox, with NO per-monitor
  selection (no monitor id anywhere), NO timestamp, NO
  freshness guarantee, NO region validation (a bbox outside
  the screen is whatever Pillow does), and
  ``capture_native_image`` (``core/desktop/vision_manager.py:144``)
  raises whatever the grab raises.
* DXcam's licence was already recorded as CLEAR-SEAT
  (``core/pilot_licences_senses.py:198``: dxcam 0.3.0, MIT,
  © 2022 Rain, sdist LICENSE + PKG-INFO License-Expression)
  but it was NOT declared in requirements.txt/lock or
  THIRD_PARTY_LICENSES.md, and it was installed in NEITHER
  interpreter (both report ModuleNotFoundError).

GAPS-19 closed the D-7 half (the engine is now driven: the
vision + skills paths call it) and the D-8 half (ImageGrab is
now one backend behind this owner, not eight direct call
sites). What it did NOT close: the bake-off winner is still
undecided — live Windows trials are still owed, and a trial
cannot declare a winner on Linux probe data. The occluded-
window class still has no WGC engine (capture.py).

OWNER (GAPS-19) — where every frame comes from
------------------------------------------------
Backend selection, per call, in order: an explicitly injected
backend (tests / the prover) > the real DXcam backend > the
ImageGrab fallback backend. ``engine_available()`` /
``engine_status()`` report the OWNER: available when ANY
backend can be built, naming which one ("dxcam" or
"imagegrab"); ``dxcam_available()`` reports the DXcam engine
specifically. Every frame dict carries "backend": the engine
that actually served it ("dxcam", "imagegrab", or "injected"
for a test backend).

``grab(monitor, region)`` is the monitor-relative entry (the
B-D5 contract, unchanged). ``grab_virtual(bbox)`` is the
virtual-screen entry every caller in the tree uses: bbox is
(left, top, right, bottom) in virtual-screen pixels, None is
the whole virtual desktop. A bbox inside ONE monitor is
grabbed from that monitor through the selected backend (the
DXcam fast path when DXcam serves); a bbox spanning monitors
is grabbed through the ImageGrab backend, which is the only
engine that natively serves a multi-monitor rectangle — the
frame dict says "imagegrab" so the caller knows. A bbox
outside the virtual screen is refused, never clamped.

``virtual_screen_bounds()`` is the one size probe: the union
of the selected backend's outputs, or None — no caller grabs
a full frame just to measure the screen any more.

Boundaries, pinned by design (not oversights):
* ``core/platform/macos.py`` / ``linux.py`` keep their own
  ImageGrab screenshot hooks: they ARE the platform backend
  seam (v5.50 items 41/42), the OS layer — the owner sits
  above them, not around them.
* The w5 ladder (``core/w5_wiring.py`` resolve/select) is a
  REPORTING layer (which bake-off rung would serve), not a
  grab site; it does not route captures and this module does
  not replace it.
* The ImageGrab backend's loop is repeated one-shot grabs in
  a daemon thread: it keeps the latest frame, it does not
  promise the target rate (a GDI grab takes ~100 ms).
* On non-Windows the ImageGrab backend measures the screen
  with one probe grab (a full grab whose pixels are
  discarded — the only size source in the tree on X11), so a
  one-shot grab there costs two grabs, not one. The Windows
  host (the product's host) measures from the monitor list
  and pays nothing extra.

What DXcam adds: Desktop Duplication frames (the OS hands
over only changed rectangles), per-output cameras, region
grabs, and a threaded capture loop with a target FPS — the
"see my screen" primitive the B5 vision path and the
see-my-working roadmap step call. This module is the new
driver surface for screen frames. It does NOT rewire
``capture_native_image`` (its ImageGrab path stays exactly
as it is) and does NOT change what B5 does with a frame
(vision analysis) — only where a frame CAN come from. The
bake-off winner decision stays open: live Windows trials
are still owed, and a trial cannot declare a winner on
Linux probe data.

LOPHOLE FAMILY — "a screenshot that shows the wrong screen"
-----------------------------------------------------------
* A grab that returns a STALE frame (the last frame from
  before the screen changed, presented as current).
* A grab of the WRONG monitor (asked for monitor 2, got 1).
* A region grab that silently CLAMPS to a different
  rectangle (asked for a rectangle partly off-screen, got
  another one back).
* A grab that FAILS and returns a black/empty frame as if
  it worked.
* A grab loop that BLOCKS the machine (an uncapped capture
  loop eating the CPU).

ONE design closes the class: every capture goes through
this module; every frame carries its capture timestamp and
monitor id (a frame is never returned without them); a
``grab()`` is FRESH (captured at call time with
``new_frame_only=True`` — DXcam's cached-frame fallback is
never used for it); monitor and region are validated BEFORE
the grab (an unknown monitor or a region outside the
monitor is refused with the reason, never silently
re-targeted or clamped); a failed grab returns an honest
failure (never a black frame, never an empty image — a
black screen is a VALID frame and is returned as ok; a
failure is a dict, never pixels); a continuous capture is
BOUNDED (a frame-rate cap and a duration cap, both stated).
``latest_frame()`` is explicitly the latest buffered frame
and says so (``"source": "latest-buffered"`` with its own
timestamp) — it is never presented as a fresh grab.

Rejected alternatives (recorded):
* Returning DXcam's cached frame as current (``grab`` with
  ``new_frame_only=False`` reuses the last frame for the
  same region — stale is a lie).
* Silent monitor fallback (asked for 2, got 1 — the B-D1
  window wrapper's primary fallback is right for windows,
  wrong for pixels).
* Silent region clamping (asked for a rectangle, got
  another — the caller asked for those pixels).
* Black-frame-as-success (a failure that looks like a
  screenshot).
* An uncapped capture loop (no rate/duration bound — a
  runaway thread eating the machine).

DEPENDENCY (APPROVED — parent, 2026-11-09)
-------------------------------------------
DXcam 0.3.0, MIT (sdist LICENSE file, Copyright (c) 2022
Rain; PKG-INFO License-Expression: MIT — read from the
0.3.0 sdist 2026-11-09, matching the pilot record).
Declared as ``dxcam>=0.3.0; sys_platform == 'win32'`` in
requirements.txt + ``dxcam==0.3.0`` in requirements.lock +
a THIRD_PARTY_LICENSES.md row (APPROVED new runtime
dependency — parent, 2026-11-09). The B-D5 docstring's
PROPOSED marker was stale and is corrected here (GAPS-19):
the parent ruled it on 2026-11-09; the declaration landed
with the ruling. Transitives: comtypes +
numpy, BOTH already declared / in the tree (numpy 2.5.4);
no GPL anywhere. The ``cv2`` processor backend is NOT
used — the wrapper passes ``processor_backend="numpy"``,
so opencv-python is never required. Weight, measured
2026-11-09 from the PyPI sdist (there is NO wheel for
0.3.0 — sdist-only, it builds from source on install):
sdist 48,742 B, unpacked 219,345 B (~214 KB). No weight
concern. The Windows build machine builds from the sdist;
``requires-python >= 3.10``, the tree is 3.12.

The engines are DXcam (real Desktop Duplication
frames) first and Pillow ImageGrab second (the GAPS-19
fallback backend, same protocol, GDI frames). On
non-Windows, or when DXcam is absent, every call is served
by the ImageGrab backend where a display exists, and fails
honestly (never pixels) where none does — the Linux lab
runs the logic against an injected fake backend (see the
backend protocol below) or the real ImageGrab backend; the
real-Windows leg (real frames at 240 Hz on his machine) is
a field leg, owed, not this batch.

BACKEND PROTOCOL (what a backend must provide)
----------------------------------------------
A backend is any object with these methods; the real ones are
``_DxcamBackend`` (DXcam, lazy-imported) and
``_ImageGrabBackend`` (Pillow ImageGrab, lazy-imported), the
tests and the prover inject a fake with the same shape:

* ``backend_name`` -> "dxcam" | "imagegrab" (an injected
  backend without the attribute reports as "injected").
* ``outputs()`` -> list of dicts, one per monitor:
  {"index": int, "x": int, "y": int, "width": int,
  "height": int, "primary": bool}. Geometry in virtual-
  screen pixels; the index is the DXcam output_idx (and the
  ImageGrab backend's monitor index on the same geometry).
* ``grab_frame(output, region)`` -> numpy array (H, W, 3)
  RGB uint8, FRESH (the backend must not return a cached
  frame), or None when no new frame is available. Region
  is monitor-relative (left, top, right, bottom), already
  validated by the wrapper.
* ``grab_virtual(bbox)`` -> numpy array (H, W, 3) RGB uint8
  for a VIRTUAL-screen bbox (left, top, right, bottom), or
  the whole virtual desktop when bbox is None. Only the
  ImageGrab backend implements this natively (it is the
  multi-monitor path); ``grab_virtual`` in this module calls
  it for a bbox spanning monitors.
* ``start_loop(output, region, target_fps)`` -> None.
* ``latest_loop_frame()`` -> (array, timestamp) or None.
* ``stop_loop()`` -> None.
* ``is_loop_running()`` -> bool.

A backend raises ``CaptureFailed`` when a grab or the loop
fails in a way the wrapper should report honestly; any
other backend failure propagates as the exception it is,
and the wrapper turns it into an honest failure dict —
never a success claim, never pixels.

Public functions take an optional ``backend`` (None = the
real selection: DXcam, else ImageGrab). An explicitly
injected backend IS the engine under test, so the
platform/DXcam availability check is skipped for it — the
suite and the prover run the logic on the fake while the
real backend is honestly unavailable on this machine.

A frame dict is {"ok", "frame", "timestamp", "monitor",
"region", "width", "height", "source", "reason", "backend"}.
``frame`` is a numpy RGB array (H, W, 3) uint8 — the
currency DXcam returns natively (``output_color="RGB"``),
and the tree already has numpy; ``frame_to_pil`` converts
one to the Pillow image the vision path consumes.
``timestamp`` is time.monotonic() seconds at capture (a
clock object with ``monotonic()``/``sleep()`` may be
injected for tests; the default is real time).
``source`` is "fresh-grab" or "latest-buffered".
``backend`` is the engine that served the frame ("dxcam",
"imagegrab", or "injected"); "monitor" is the monitor index
for a single-monitor grab and None for a multi-monitor
virtual grab, whose "region" is the virtual bbox.
"""

from __future__ import annotations

import sys
import threading
import time

# A loop that asks for more than this is refused, never
# silently slowed — the caller asked for a rate the design
# does not run.
MAX_TARGET_FPS = 240
MIN_TARGET_FPS = 1
# A single bounded collection never runs longer than this.
MAX_COLLECT_SECONDS = 3600.0
MAX_COLLECT_FRAMES = 10000


class _EngineUnavailable(Exception):
    """DXcam (or Windows) is not available for real capture."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class CaptureFailed(Exception):
    """A backend grab/loop failure the wrapper reports honestly."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


# ----------------------------------------------------------------
# Clock (real time by default; a fake clock drives the loop
# in tests without any real waiting)
# ----------------------------------------------------------------

class _RealClock:
    """time.monotonic / time.sleep, as an object."""

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)


# ----------------------------------------------------------------
# Engine availability (real backend only)
# ----------------------------------------------------------------

def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _real_backend():
    """The real DXcam backend, or raise _EngineUnavailable
    naming what is missing. DXcam is imported lazily, here —
    importing THIS module never touches it (the sympy pattern)."""
    if not _is_windows():
        raise _EngineUnavailable(
            "screen capture needs Windows Desktop Duplication "
            "(DXcam) — this system is not Windows")
    try:
        import dxcam  # noqa: F401 — the import IS the probe
    except ImportError as e:
        raise _EngineUnavailable(
            "DXcam is not available in this build of VYUK — "
            f"the capture part didn't make it into the package ({e})"
        ) from e
    return _DxcamBackend()


def _imagegrab_module():
    """The Pillow ImageGrab module, or raise _EngineUnavailable.
    The test seam for the ImageGrab backend: patch this
    function to stand in a fake grab (the suite and the prover
    do), never the backend class itself."""
    try:
        from PIL import ImageGrab
    except ImportError as e:
        raise _EngineUnavailable(
            "Pillow ImageGrab is not available in this build of "
            f"VYUK — the image part didn't make it into the package ({e})"
        ) from e
    return ImageGrab


def _select_backend(backend):
    """The backend that serves this call, in order: an
    explicitly injected backend > the real DXcam backend > the
    ImageGrab fallback backend. Raises _EngineUnavailable only
    when none can be built (no DXcam and no Pillow)."""
    if backend is not None:
        return backend
    try:
        return _real_backend()
    except _EngineUnavailable:
        pass
    _imagegrab_module()  # raises _EngineUnavailable when Pillow is missing
    return _ImageGrabBackend()


def dxcam_available() -> dict:
    """The DXcam engine specifically: {"available", "backend",
    "error"}. False on non-Windows and where DXcam is not
    installed — the owner may still capture through the
    ImageGrab fallback (see engine_status)."""
    try:
        _real_backend()
    except _EngineUnavailable as e:
        return {"available": False, "backend": None,
                "error": e.detail}
    return {"available": True, "backend": "dxcam", "error": None}


def engine_available() -> bool:
    """True when the owner can capture: any backend can be built
    (DXcam, else the ImageGrab fallback). False only when
    neither engine exists on this host — and that is an honest
    answer, not a skip. (GAPS-19: this used to report the DXcam
    engine only; the owner reports the selection now.)"""
    try:
        _select_backend(None)
    except _EngineUnavailable:
        return False
    return True


def engine_status() -> dict:
    """Honest owner report: {"available", "backend", "error"}.
    "backend" names the engine that would serve ("dxcam" or
    "imagegrab"). Building a backend is not a grab: on a
    headless host the status can say available while a grab
    still fails honestly for want of a display."""
    try:
        be = _select_backend(None)
    except _EngineUnavailable as e:
        return {"available": False, "backend": None,
                "error": e.detail}
    return {"available": True,
            "backend": getattr(be, "backend_name", "injected"),
            "error": None}


# ----------------------------------------------------------------
# The real backend — DXcam, and nothing else
# ----------------------------------------------------------------

class _DxcamBackend:
    """Desktop Duplication frames via DXcam.

    Monitor geometry comes from the B-D1 PyMonCtl wrapper
    (``core/desktop/pywinctl_wrapper.py`` ``monitors()``) —
    the already-approved monitor source — and the DXcam
    output_idx is that monitor's index on the same host.
    The camera is created per grab (one-shot path) or held
    for a loop; ``output_color="RGB"`` and
    ``processor_backend="numpy"`` so no OpenCV is ever
    required. ``new_frame_only=True`` is passed explicitly
    on every one-shot grab: DXcam's cached-frame fallback
    (``new_frame_only=False``) is never used for a grab.
    """

    backend_name = "dxcam"

    def outputs(self) -> list:
        from .pywinctl_wrapper import monitors as _monitors

        listed = _monitors()
        if not listed["ok"]:
            raise CaptureFailed(
                "could not read the monitor list: "
                + str(listed.get("reason")))
        out = []
        for m in listed["monitors"]:
            out.append({
                "index": int(m["index"]),
                "x": int(m["left"]),
                "y": int(m["top"]),
                "width": int(m["width"]),
                "height": int(m["height"]),
                "primary": bool(m["primary"]),
            })
        return out

    @staticmethod
    def _make_camera(output: int, region):
        import dxcam

        return dxcam.create(
            output_idx=int(output),
            region=region,
            output_color="RGB",
            processor_backend="numpy",
        )

    def grab_frame(self, output: int, region):
        """One FRESH frame (new_frame_only=True), or None."""
        camera = self._make_camera(output, region)
        try:
            return camera.grab(new_frame_only=True)
        finally:
            try:
                camera.release()
            except Exception:  # noqa: BLE001 — release is
                # best-effort; the grab already returned.
                pass

    def __init__(self):
        self._loop_camera = None

    def start_loop(self, output: int, region, target_fps: int) -> None:
        if self._loop_camera is not None:
            raise CaptureFailed(
                "a capture loop is already running — stop it first")
        camera = self._make_camera(output, region)
        try:
            camera.start(region=region, target_fps=int(target_fps))
        except Exception:
            try:
                camera.release()
            except Exception:  # noqa: BLE001
                pass
            raise
        self._loop_camera = camera

    def latest_loop_frame(self):
        if self._loop_camera is None:
            return None
        got = self._loop_camera.get_latest_frame(with_timestamp=True)
        if got is None:
            return None
        frame, stamp = got
        return frame, float(stamp)

    def stop_loop(self) -> None:
        camera, self._loop_camera = self._loop_camera, None
        if camera is None:
            return
        try:
            camera.stop()
        finally:
            try:
                camera.release()
            except Exception:  # noqa: BLE001
                pass

    def is_loop_running(self) -> bool:
        return self._loop_camera is not None


# ----------------------------------------------------------------
# The fallback backend — Pillow ImageGrab, same protocol
# ----------------------------------------------------------------

class _ImageGrabBackend:
    """GDI frames via Pillow ImageGrab — the fallback backend
    (GAPS-19). The same protocol as the DXcam backend, so the
    wrapper's validation, timestamping and failure contract
    hold whichever engine serves.

    Geometry: on Windows, the same PyMonCtl monitor list the
    DXcam backend uses, so both engines agree on what the
    monitors are; elsewhere, one virtual monitor measured from
    a probe grab (a full grab whose pixels are discarded — the
    only size source in the tree on X11). ``grab_frame``
    translates the monitor-relative region to virtual
    coordinates and grabs with ``all_screens=True`` (with the
    plain-grab fallback for Pillow builds without the
    keyword). ``grab_virtual`` grabs a virtual bbox directly —
    the multi-monitor path, where no per-output camera can
    serve. The loop is repeated one-shot grabs in a daemon
    thread: it keeps the latest frame, it does not promise
    the target rate (a GDI grab takes ~100 ms).
    """

    backend_name = "imagegrab"

    def __init__(self):
        self._size = None  # (width, height), measured once, non-Windows
        self._loop_thread = None
        self._loop_stop = None
        self._loop_lock = threading.Lock()
        self._loop_latest = None  # (array, timestamp) or None
        self._loop_output = None
        self._loop_region = None

    def outputs(self) -> list:
        if _is_windows():
            from .pywinctl_wrapper import monitors as _monitors

            listed = _monitors()
            if not listed["ok"]:
                raise CaptureFailed(
                    "could not read the monitor list: "
                    + str(listed.get("reason")))
            return [{
                "index": int(m["index"]),
                "x": int(m["left"]),
                "y": int(m["top"]),
                "width": int(m["width"]),
                "height": int(m["height"]),
                "primary": bool(m["primary"]),
            } for m in listed["monitors"]]
        if self._size is None:
            grab = _imagegrab_module()
            with grab.grab() as full:
                self._size = (int(full.width), int(full.height))
        width, height = self._size
        return [{"index": 0, "x": 0, "y": 0, "width": width,
                 "height": height, "primary": True}]

    @staticmethod
    def _to_array(image):
        """A grabbed PIL image -> a fresh (H, W, 3) RGB uint8
        array. np.array (a copy): the array must survive the
        image it came from."""
        import numpy as np

        if image.mode != "RGB":
            image = image.convert("RGB")
        return np.array(image, dtype=np.uint8)

    def grab_frame(self, output: int, region):
        """One FRESH frame for a monitor-relative region."""
        outs = self.outputs()
        out = next((o for o in outs if o["index"] == int(output)),
                   None)
        if out is None:
            raise CaptureFailed(
                f"No monitor {output} — monitors here: "
                f"{[o['index'] for o in outs]}.")
        left, top, right, bottom = (int(v) for v in region)
        bbox = (int(out["x"]) + left, int(out["y"]) + top,
                int(out["x"]) + right, int(out["y"]) + bottom)
        grab = _imagegrab_module()
        try:
            image = grab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            image = grab.grab(bbox=bbox)
        return self._to_array(image)

    def grab_virtual(self, bbox):
        """One FRESH frame for a virtual-screen bbox, or the
        whole virtual desktop when bbox is None. The only
        engine that serves a multi-monitor rectangle."""
        grab = _imagegrab_module()
        try:
            if bbox is None:
                image = grab.grab(all_screens=True)
            else:
                image = grab.grab(bbox=bbox, all_screens=True)
        except TypeError:
            if bbox is None:
                image = grab.grab()
            else:
                image = grab.grab(bbox=bbox)
        return self._to_array(image)

    def start_loop(self, output: int, region, target_fps: int) -> None:
        if self._loop_thread is not None \
                and self._loop_thread.is_alive():
            raise CaptureFailed(
                "a capture loop is already running — stop it first")
        stop = threading.Event()
        self._loop_stop = stop
        self._loop_output = int(output)
        self._loop_region = tuple(int(v) for v in region)
        self._loop_latest = None

        def _run():
            interval = 1.0 / max(1, int(target_fps))
            while not stop.is_set():
                try:
                    frame = self.grab_frame(self._loop_output,
                                            self._loop_region)
                except Exception:  # noqa: BLE001 — a failed
                    # loop grab keeps the last good frame; the
                    # loop itself never dies on a grab.
                    frame = None
                if frame is not None:
                    with self._loop_lock:
                        self._loop_latest = (frame, time.monotonic())
                stop.wait(interval)

        thread = threading.Thread(target=_run, daemon=True,
                                  name="imagegrab-capture-loop")
        self._loop_thread = thread
        thread.start()

    def latest_loop_frame(self):
        with self._loop_lock:
            return self._loop_latest

    def stop_loop(self) -> None:
        stop, self._loop_stop = self._loop_stop, None
        thread, self._loop_thread = self._loop_thread, None
        self._loop_output = None
        self._loop_region = None
        if stop is not None:
            stop.set()
        if thread is not None:
            thread.join(timeout=2.0)

    def is_loop_running(self) -> bool:
        return self._loop_thread is not None \
            and self._loop_thread.is_alive()


# ----------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------

def _clock_or_real(clock):
    return clock if clock is not None else _RealClock()


def _virtual_bounds(outputs: list):
    """(left, top, right, bottom) of the union of the outputs,
    or None when there are none."""
    if not outputs:
        return None
    return (min(int(o["x"]) for o in outputs),
            min(int(o["y"]) for o in outputs),
            max(int(o["x"]) + int(o["width"]) for o in outputs),
            max(int(o["y"]) + int(o["height"]) for o in outputs))


def _rects_overlap(a, b) -> bool:
    """True when rectangles a and b (left, top, right, bottom)
    share at least one pixel."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _validate_virtual_bbox(outputs: list, bbox):
    """A virtual-screen bbox (left, top, right, bottom), fully
    inside the virtual desktop — or a refusal. Never clamped:
    the caller asked for those pixels."""
    if isinstance(bbox, (str, bytes)) or not hasattr(bbox, "__len__") \
            or len(bbox) != 4:
        return None, (
            "Region must be (left, top, right, bottom) whole "
            f"numbers (got {bbox!r}).")
    try:
        left, top, right, bottom = (int(v) for v in bbox)
    except (TypeError, ValueError):
        return None, (
            "Region must be (left, top, right, bottom) whole "
            f"numbers (got {bbox!r}).")
    bounds = _virtual_bounds(outputs)
    if bounds is None:
        return None, "Could not read the monitor list: no monitors reported."
    if left < bounds[0] or top < bounds[1] \
            or right <= left or bottom <= top:
        return None, (
            f"Region {(left, top, right, bottom)} is not "
            "a rectangle inside the virtual screen "
            f"{bounds} (left < right and top < bottom, "
            "from the virtual origin).")
    if right > bounds[2] or bottom > bounds[3]:
        return None, (
            f"Region {(left, top, right, bottom)} is outside "
            f"the virtual screen {bounds} — it is refused, "
            "never clamped.")
    return (left, top, right, bottom), None


def _fail(reason: str, backend=None, **extra) -> dict:
    out = {"ok": False, "frame": None, "timestamp": None,
           "monitor": None, "region": None, "width": None,
           "height": None, "source": None, "reason": reason,
           "backend": backend}
    out.update(extra)
    return out


def _check_frame(frame, what: str):
    """A grabbed array is a real frame, or it is a failure.
    A BLACK frame is a valid frame (a black screen is real
    pixels) — only a missing/empty/misshapen array fails.
    The wrapper never fabricates pixels: a failure is a dict,
    never an array."""
    if frame is None:
        return None, f"{what} returned no frame"
    try:
        import numpy as np

        arr = np.asarray(frame)
    except Exception as e:  # noqa: BLE001
        return None, f"{what} returned an unreadable frame: {e}"
    if arr.ndim != 3 or arr.shape[2] != 3:
        return None, (
            f"{what} returned a frame with shape {arr.shape} — "
            "expected (height, width, 3) RGB")
    if arr.shape[0] <= 0 or arr.shape[1] <= 0:
        return None, f"{what} returned an empty frame {arr.shape}"
    return arr, None


def _validate_monitor(outputs: list, monitor):
    """The monitor index, or a refusal naming it. Never a
    silent fallback to another monitor."""
    if isinstance(monitor, bool) or not isinstance(monitor, int):
        try:
            monitor = int(monitor)
        except (TypeError, ValueError):
            return None, (
                "Monitor must be a whole number "
                f"(got {monitor!r}).")
    known = [o["index"] for o in outputs]
    if monitor not in known:
        return None, (
            f"No monitor {monitor} — monitors here: {known}.")
    return monitor, None


def _validate_region(outputs: list, monitor: int, region):
    """A monitor-relative region (left, top, right, bottom),
    fully inside the monitor — or a refusal. Never clamped:
    the caller asked for those pixels."""
    if region is None:
        out = next(o for o in outputs if o["index"] == monitor)
        return (0, 0, int(out["width"]), int(out["height"])), None
    if isinstance(region, (str, bytes)) or not hasattr(region, "__len__") \
            or len(region) != 4:
        return None, (
            "Region must be (left, top, right, bottom) whole "
            f"numbers (got {region!r}).")
    try:
        left, top, right, bottom = (int(v) for v in region)
    except (TypeError, ValueError):
        return None, (
            "Region must be (left, top, right, bottom) whole "
            f"numbers (got {region!r}).")
    out = next(o for o in outputs if o["index"] == monitor)
    width, height = int(out["width"]), int(out["height"])
    if left < 0 or top < 0 or right <= left or bottom <= top:
        return None, (
            f"Region {tuple((left, top, right, bottom))} is not "
            "a rectangle (left < right and top < bottom, "
            "from 0).")
    if right > width or bottom > height:
        return None, (
            f"Region {(left, top, right, bottom)} is outside "
            f"monitor {monitor} ({width}x{height}) — it is "
            "refused, never clamped.")
    return (left, top, right, bottom), None


def _frame_dict(arr, timestamp, monitor, region, source, backend) -> dict:
    return {"ok": True, "frame": arr, "timestamp": float(timestamp),
            "monitor": monitor, "region": region,
            "width": int(arr.shape[1]), "height": int(arr.shape[0]),
            "source": source, "reason": "", "backend": backend}


def frame_to_pil(frame):
    """A captured RGB array -> the Pillow image the vision path
    consumes. Validates the shape first; a bad array raises
    ValueError rather than producing a wrong image."""
    arr, err = _check_frame(frame, "frame_to_pil")
    if err:
        raise ValueError(err)
    from PIL import Image

    return Image.fromarray(arr.astype("uint8"), "RGB")


# ----------------------------------------------------------------
# Monitors
# ----------------------------------------------------------------

def list_monitors(backend=None) -> dict:
    """Every monitor the owner can capture, with geometry:
    {"ok", "monitors": [{index, x, y, width, height,
    primary}], "reason"}."""
    try:
        be = _select_backend(backend)
        outputs = be.outputs()
    except _EngineUnavailable as e:
        return {"ok": False, "monitors": [], "reason": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "monitors": [],
                "reason": f"Could not read the monitor list: {e}"}
    monitors = [{
        "index": int(o["index"]), "x": int(o["x"]),
        "y": int(o["y"]), "width": int(o["width"]),
        "height": int(o["height"]),
        "primary": bool(o.get("primary", False)),
    } for o in outputs]
    return {"ok": True, "monitors": monitors, "reason": ""}


# ----------------------------------------------------------------
# One fresh frame
# ----------------------------------------------------------------

def grab(monitor: int = 0, region=None, backend=None,
         clock=None) -> dict:
    """One FRESH frame from one monitor (optionally a region
    of it). The frame is captured at call time with
    ``new_frame_only=True`` — never a cached frame from an
    earlier call. Returns a frame dict (see the module
    docstring); a failed grab is ok False with the reason,
    never pixels."""
    now = _clock_or_real(clock)
    try:
        be = _select_backend(backend)
        outputs = be.outputs()
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}")
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not read the monitor list: {e}")
    name = getattr(be, "backend_name", "injected")
    monitor, err = _validate_monitor(outputs, monitor)
    if err:
        return _fail(err, backend=name)
    region, err = _validate_region(outputs, monitor, region)
    if err:
        return _fail(err, backend=name, monitor=monitor)
    try:
        raw = be.grab_frame(monitor, region)
    except CaptureFailed as e:
        return _fail(e.detail, backend=name, monitor=monitor,
                     region=region)
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not grab a frame: {type(e).__name__}: {e}",
                     backend=name, monitor=monitor, region=region)
    arr, err = _check_frame(raw, "grab")
    if err:
        return _fail(err + " — no new frame was available",
                     backend=name, monitor=monitor, region=region)
    return _frame_dict(arr, now.monotonic(), monitor, region,
                       "fresh-grab", name)


# ----------------------------------------------------------------
# One fresh frame, virtual-screen coordinates
# ----------------------------------------------------------------

def grab_virtual(bbox=None, backend=None, clock=None) -> dict:
    """One FRESH frame for a VIRTUAL-screen bbox (left, top,
    right, bottom), or the whole virtual desktop when bbox is
    None. This is the entry every caller in the tree uses:
    the vision path, the skills capture tools, the hands
    seam and the social-send guard all speak virtual pixels.

    A bbox inside ONE monitor is grabbed from that monitor
    through the selected backend (the DXcam fast path when
    DXcam serves). A bbox spanning monitors — including the
    whole virtual desktop on a multi-monitor host — is
    grabbed through the ImageGrab backend, the only engine
    that natively serves a multi-monitor rectangle; the frame
    dict then says "imagegrab" with monitor None and the
    virtual bbox as its region. A bbox outside the virtual
    screen is refused, never clamped. Returns a frame dict
    (see the module docstring); a failed grab is ok False
    with the reason, never pixels."""
    now = _clock_or_real(clock)
    try:
        be = _select_backend(backend)
        outputs = be.outputs()
    except _EngineUnavailable as e:
        return _fail(f"engine_unavailable: {e.detail}")
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not read the monitor list: {e}")
    name = getattr(be, "backend_name", "injected")
    if bbox is None:
        vbox = _virtual_bounds(outputs)
        if vbox is None:
            return _fail("Could not read the monitor list: "
                         "no monitors reported", backend=name)
    else:
        vbox, err = _validate_virtual_bbox(outputs, bbox)
        if err:
            return _fail(err, backend=name)
    hit = [o for o in outputs
           if _rects_overlap(vbox, (int(o["x"]), int(o["y"]),
                                    int(o["x"]) + int(o["width"]),
                                    int(o["y"]) + int(o["height"])))]
    if len(hit) == 1:
        out = hit[0]
        monitor = int(out["index"])
        region = (vbox[0] - int(out["x"]), vbox[1] - int(out["y"]),
                  vbox[2] - int(out["x"]), vbox[3] - int(out["y"]))
        try:
            raw = be.grab_frame(monitor, region)
        except CaptureFailed as e:
            return _fail(e.detail, backend=name, monitor=monitor,
                         region=region)
        except Exception as e:  # noqa: BLE001
            return _fail(
                f"Could not grab a frame: {type(e).__name__}: {e}",
                backend=name, monitor=monitor, region=region)
        arr, err = _check_frame(raw, "grab_virtual")
        if err:
            return _fail(err + " — no new frame was available",
                         backend=name, monitor=monitor, region=region)
        return _frame_dict(arr, now.monotonic(), monitor, region,
                           "fresh-grab", name)
    # Multi-monitor rectangle: the ImageGrab backend serves it.
    if name == "imagegrab":
        ig = be
    else:
        try:
            ig = _ImageGrabBackend()
            _imagegrab_module()  # Pillow present, or refuse honestly
        except _EngineUnavailable as e:
            return _fail(f"engine_unavailable: {e.detail}",
                         backend=name)
    try:
        raw = ig.grab_virtual(vbox)
    except CaptureFailed as e:
        return _fail(e.detail, backend="imagegrab", monitor=None,
                     region=vbox)
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not grab a frame: {type(e).__name__}: {e}",
                     backend="imagegrab", monitor=None, region=vbox)
    arr, err = _check_frame(raw, "grab_virtual")
    if err:
        return _fail(err + " — no new frame was available",
                     backend="imagegrab", monitor=None, region=vbox)
    return _frame_dict(arr, now.monotonic(), None, vbox,
                       "fresh-grab", "imagegrab")


def virtual_screen_bounds(backend=None):
    """(left, top, right, bottom) of the whole virtual desktop,
    or None when it cannot be determined. Never raises — this
    is the one size probe; no caller grabs a full frame just
    to measure the screen any more."""
    try:
        be = _select_backend(backend)
        outputs = be.outputs()
    except Exception:  # noqa: BLE001 — unmeasurable is None
        return None
    return _virtual_bounds(outputs)


# ----------------------------------------------------------------
# The bounded loop: start / latest / stop / collect
# ----------------------------------------------------------------

# One loop at a time per process (the DXcam camera is a
# single threaded capture; a second loop is a second camera
# on the same output). The state is the loop's, not a
# global capture — a driver that needs two loops says so
# with a second backend.
_LOOP = {"backend": None, "monitor": None, "region": None,
         "target_fps": None, "started_at": None}


def _loop_state() -> dict:
    return _LOOP


def start_capture(monitor: int = 0, region=None, target_fps: int = 30,
                  backend=None, clock=None) -> dict:
    """Start the threaded capture loop on one monitor (and
    region). target_fps is 1..240 — outside it is refused,
    never silently slowed. Returns {"ok", "monitor", "region",
    "target_fps", "reason"}. A second start while one runs is
    refused (stop it first)."""
    now = _clock_or_real(clock)
    if _LOOP["backend"] is not None:
        return {"ok": False, "monitor": _LOOP["monitor"],
                "region": _LOOP["region"], "target_fps": None,
                "reason": "a capture loop is already running — "
                          "stop it first"}
    if isinstance(target_fps, bool) or not isinstance(target_fps, int):
        try:
            target_fps = int(target_fps)
        except (TypeError, ValueError):
            return {"ok": False, "monitor": None, "region": None,
                    "target_fps": None,
                    "reason": "Target FPS must be a whole number "
                              f"(got {target_fps!r})."}
    if not MIN_TARGET_FPS <= target_fps <= MAX_TARGET_FPS:
        return {"ok": False, "monitor": None, "region": None,
                "target_fps": None,
                "reason": f"Target FPS must be between "
                          f"{MIN_TARGET_FPS} and {MAX_TARGET_FPS} "
                          f"(got {target_fps})."}
    try:
        be = _select_backend(backend)
        outputs = be.outputs()
    except _EngineUnavailable as e:
        return {"ok": False, "monitor": None, "region": None,
                "target_fps": None,
                "reason": f"engine_unavailable: {e.detail}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "monitor": None, "region": None,
                "target_fps": None,
                "reason": f"Could not read the monitor list: {e}"}
    monitor, err = _validate_monitor(outputs, monitor)
    if err:
        return {"ok": False, "monitor": None, "region": None,
                "target_fps": None, "reason": err}
    region, err = _validate_region(outputs, monitor, region)
    if err:
        return {"ok": False, "monitor": monitor, "region": None,
                "target_fps": None, "reason": err}
    try:
        be.start_loop(monitor, region, target_fps)
    except CaptureFailed as e:
        return {"ok": False, "monitor": monitor, "region": region,
                "target_fps": None, "reason": e.detail}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "monitor": monitor, "region": region,
                "target_fps": None,
                "reason": f"Could not start the capture loop: "
                          f"{type(e).__name__}: {e}"}
    _LOOP.update({"backend": be, "monitor": monitor,
                  "region": region, "target_fps": target_fps,
                  "started_at": now.monotonic()})
    return {"ok": True, "monitor": monitor, "region": region,
            "target_fps": target_fps, "reason": ""}


def latest_frame(backend=None, clock=None) -> dict:
    """The LATEST buffered frame from the running loop — with
    its own timestamp, labelled "latest-buffered". It is
    explicitly NOT a fresh grab: the caller asked for the
    latest, and the dict says so. Refuses honestly when no
    loop is running."""
    if backend is None:
        be = _LOOP["backend"]
        if be is None:
            return _fail("no capture loop is running — start one first")
    else:
        be = backend
        if _LOOP["backend"] is not be:
            return _fail("no capture loop is running on that backend — "
                         "start one first")
    try:
        got = be.latest_loop_frame()
    except CaptureFailed as e:
        return _fail(e.detail)
    except Exception as e:  # noqa: BLE001
        return _fail(f"Could not read the latest frame: "
                     f"{type(e).__name__}: {e}")
    if got is None:
        return _fail("the capture loop has no frame buffered yet",
                     backend=getattr(be, "backend_name", "injected"))
    frame, stamp = got
    arr, err = _check_frame(frame, "latest_frame")
    if err:
        return _fail(err,
                     backend=getattr(be, "backend_name", "injected"))
    return _frame_dict(arr, float(stamp), _LOOP["monitor"],
                       _LOOP["region"], "latest-buffered",
                       getattr(be, "backend_name", "injected"))


def stop_capture(backend=None) -> dict:
    """Stop the running loop (and release its camera).
    Returns {"ok", "reason"}; stopping when nothing runs is
    ok True with a note, never an error. A backend that is
    not the running loop's is never stopped, and the loop
    state is cleared only for the loop that was stopped."""
    if backend is None:
        be = _LOOP["backend"]
        if be is None:
            return {"ok": True, "reason": "no capture loop was running"}
    else:
        be = backend
        if _LOOP["backend"] is not be:
            return {"ok": True,
                    "reason": "no capture loop was running on that "
                              "backend"}
    try:
        be.stop_loop()
    except Exception as e:  # noqa: BLE001 — the loop is
        # stopped from the wrapper's side regardless.
        _LOOP.update({"backend": None, "monitor": None,
                      "region": None, "target_fps": None,
                      "started_at": None})
        return {"ok": False,
                "reason": f"Could not stop the capture loop cleanly: {e}"}
    _LOOP.update({"backend": None, "monitor": None, "region": None,
                  "target_fps": None, "started_at": None})
    return {"ok": True, "reason": ""}


def collect_frames(monitor: int = 0, region=None, duration_s: float = 1.0,
                   target_fps: int = 30, max_frames: int = 100,
                   backend=None, clock=None) -> dict:
    """A BOUNDED synchronous collection: run the loop for at
    most ``duration_s`` seconds at ``target_fps`` frames per
    second, collecting at most ``max_frames`` frames, then
    stop. Returns {"ok", "frames": [frame dicts], "count",
    "duration_s", "reason"}. The bounds are the design: a
    collection cannot run forever and cannot eat the machine
    — duration is 0.1..3600 s, target_fps 1..240, max_frames
    1..10000, all refused outside their range, never clamped."""
    now = _clock_or_real(clock)
    for name, value, lo, hi in (
            ("duration_s", duration_s, 0.1, MAX_COLLECT_SECONDS),
            ("target_fps", target_fps, MIN_TARGET_FPS, MAX_TARGET_FPS),
            ("max_frames", max_frames, 1, MAX_COLLECT_FRAMES)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"ok": False, "frames": [], "count": 0,
                    "duration_s": 0.0,
                    "reason": f"{name} must be a number "
                              f"(got {value!r})."}
        if not lo <= value <= hi:
            return {"ok": False, "frames": [], "count": 0,
                    "duration_s": 0.0,
                    "reason": f"{name} must be between {lo} and "
                              f"{hi} (got {value})."}
    started = start_capture(monitor=monitor, region=region,
                            target_fps=int(target_fps),
                            backend=backend, clock=clock)
    if not started["ok"]:
        return {"ok": False, "frames": [], "count": 0,
                "duration_s": 0.0, "reason": started["reason"]}
    frames = []
    try:
        begin = now.monotonic()
        while (len(frames) < int(max_frames)
               and now.monotonic() - begin < float(duration_s)):
            got = latest_frame(clock=clock)
            if got["ok"]:
                frames.append(got)
            now.sleep(1.0 / int(target_fps))
    finally:
        stop_capture()
    return {"ok": True, "frames": frames, "count": len(frames),
            "duration_s": float(duration_s), "reason": ""}
