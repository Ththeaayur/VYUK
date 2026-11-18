"""VisionManager + singleton + instance-dependent helpers (skills split B16).

Moved verbatim from core/desktop/vision.py. capture_geometry_line,
marks_table_text and the module-level tails resolve `vision_manager`
by bare name, so they live in the module that owns the instance —
placing them in vision_capture.py would force a manager<->capture
import cycle. Imports from .vision_capture below are the helpers the
manager methods resolve by bare name (INTENTIONAL, skills split B16).
"""

import io  # INTENTIONAL (skills split B16): capture_screen/capture_webcam resolve io by bare name

from PIL import Image  # INTENTIONAL (skills split B16): capture_screen/capture_webcam resolve Image by bare name

from .vision_capture import (  # INTENTIONAL (skills split B16): helpers the manager methods resolve by bare name
    _backend_raw_full_frame,
    _capture_block_reason,
    _cursor_position,
    _grab_image,
    _record_virtual_bounds,
    _routed_backend,
    _virtual_screen_bounds,
    _w5_note_capture,
    draw_marks_on_image,
    foreground_window_rect,
    ground_marks_for_frame,
)

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False


def _capture_foreground_hwnd():
    """Foreground window handle at capture time (C8), or None.

    Guarded: any probe failure reads as unknown, never a
    capture failure — the freshness branches skip on unknown
    identity rather than inventing a refusal."""
    try:
        from ..skills_window_control import _foreground_hwnd
        return _foreground_hwnd()
    except Exception:  # noqa: BLE001 — unknown, not a refusal
        return None


def _capture_foreground_title():
    """Foreground window title at capture time (C8), or None.

    Same guarded posture as _capture_foreground_hwnd: the
    title is the F-07 fallback when the handle is unknown on
    either side, and unknown never counts as a change."""
    try:
        from ..vision_exclusions import foreground_probe
        _proc, _title = foreground_probe()
        return _title or None
    except Exception:  # noqa: BLE001 — unknown, not a refusal
        return None


class VisionManager:
    def __init__(self):
        self.camera_index = 0
        self._cam = None
        self._last_capture_info: dict | None = None
        self._last_marks: list = []
        # Control-existence (C7/C8, 2026-11-05): monotonically
        # increasing id for each capture_screen record — the
        # capture grid's baseline for C7 capture binding.
        self._capture_counter = 0
        # C7 crop producer (2026-11-05): SINGLE-slot retained
        # sent image — the thumbnailed frame the brain actually
        # saw, grayscale — plus its capture id and record.
        # Overwritten per capture_screen call; never a history.
        # Region crops are cut from this slot on demand (see
        # core/desktop/point_crop.py); a slot holds at most one
        # thumbnail (<= max_dim per side, 1 byte/px), so the
        # bound is ~1 MB, not a frame archive.
        self._last_sent_frame = None

    def last_marks(self) -> list:
        """A copy of the marks table from the most recent
        marks=True capture ([] when none, or when that capture
        was blocked / produced none)."""
        return [dict(m) for m in self._last_marks]

    def last_capture_info(self) -> dict | None:
        """Geometry of the most recent capture_screen (v5.43, E10):
        {"screen_width", "screen_height"} — the TRUE screen size in
        pixels — and {"image_width", "image_height"} — the size of
        the image actually sent — plus the "max_dim" it was taken
        with. A copy, so callers can't mutate the record. None when
        no screen capture has happened yet. The brain-facing image
        is downscaled while the cursor takes true pixels; this
        record is how a caller states both numbers honestly.

        v5.44 (E7) adds the crop record: "cropped" plus
        "crop_left" / "crop_top" / "crop_width" / "crop_height" —
        the foreground window's rectangle the image shows, in true
        screen pixels (for an uncropped capture, the whole screen
        at (0, 0)). 2026-11-03 adds "monitor_crop" (True when the
        crop is a caller-supplied monitor rectangle, DISPLAY-01,
        rather than the foreground window). 2026-11-03 adds
        "virtual_bounds" (the virtual desktop bounds at capture
        time, None when unmeasurable). The record carries
        geometry only — no timestamp, no window identity — which
        is why the norm1000 freshness check in cursor.py compares
        geometry, not identity (see its docstring for the stated
        residuals)."""
        # v5.50 R5-V: vision's OWN record, never the backend's —
        # the pipeline (capture_screen below) records every
        # capture here, backend-grabbed frames included, and the
        # E7b/E10 consumers (local_client._describe_frame,
        # tools._ocr_captured_frame, live_client's companion
        # text) read exactly this record. Delegating to the
        # backend's separate record made those consumers blind
        # to captures this manager had just taken.
        if self._last_capture_info is None:
            return None
        return dict(self._last_capture_info)

    def last_sent_frame(self) -> dict | None:
        """Copy of the single-slot retained sent image, or None.

        {capture_id, width, height, gray (bytes, row-major),
        record (a copy of the capture's geometry record)}.
        None when no screen capture has happened yet. A copy,
        so callers can't mutate the slot; the gray bytes are
        immutable. This is the C7 crop producer's pixel supply
        (see core/desktop/point_crop.py) — the frame the brain
        actually saw, not a fresh grab.
        """
        if self._last_sent_frame is None:
            return None
        slot = self._last_sent_frame
        return {"capture_id": slot["capture_id"],
                "width": slot["width"],
                "height": slot["height"],
                "gray": slot["gray"],
                "record": dict(slot["record"])}

    def capture_native_image(self, full_screen: bool = False):
        """(PIL Image, meta): a FULL-RESOLUTION screen frame —
        never thumbnailed, never JPEG-compressed (v5.44, E2).

        The default target is the FOREGROUND window's rectangle
        (E7: the crop is the default for brain-bound and OCR
        captures alike); `full_screen=True` — or no window having
        focus — captures the whole desktop. The grab goes through
        the capture owner (core/desktop/screen_capture.py
        grab_virtual, DXcam or its ImageGrab fallback). meta states
        the true screen size, the image's native size, and the
        crop origin/size, in the same key shape last_capture_info
        records. Raises whatever the grab raises — capture failure
        is the caller's sentence to phrase.

        v5.48 (programme item 21): the sensitive-window denylist
        is checked before any grab — a denylisted foreground
        raises capture_guard.CaptureBlockedError carrying the
        honest refusal reason, so OCR / screen_look callers
        surface it as their text result through their existing
        capture-failure wording, never as a silent empty frame.
        capture_screen() funnels through here, so the one check
        covers screenshots and OCR frames alike.

        v5.50 R5-V: when a non-Windows backend serves, it
        supplies the RAW full-screen frame only (after the
        denylist check above); the E7 crop and the meta record
        are then produced here, identically to the legacy grab
        path. When the backend cannot grab (its documented
        fail-soft RuntimeError), the legacy body below runs
        unchanged (R4's fall-through)."""
        # v5.50 R5-V restructure: the denylist gate runs FIRST,
        # in this module, before ANY pixels are grabbed — by the
        # backend or by the legacy grab alike. (The items-41/42
        # delegation returned the backend's frame before this
        # check was reached, so on a backend host the Phase-3
        # denylist was bypassed entirely whenever the backend's
        # own grab could run.)
        _blocked = _capture_block_reason()
        if _blocked is not None:
            from ..capture_guard import CaptureBlockedError  # Phase 3 D4c: vision hub moved to core/desktop/

            raise CaptureBlockedError(_blocked)
        # v5.60 (PF-17): the USER exclusion list is checked here,
        # before any grab, in addition to the built-in denylist
        # above. A match refuses the capture outright — listed
        # windows/apps/regions are never captured, not blurred
        # afterwards. The crop rect is computed first so region
        # exclusions can be tested against the actual capture
        # area (full-screen uses the known virtual-screen bounds
        # when available; unknown bounds skip only the region
        # half, never the process/title halves).
        if full_screen:
            _bounds = _virtual_screen_bounds()
            if _bounds is not None:
                _check_rect = (_bounds[0], _bounds[1],
                               _bounds[2], _bounds[3])
            else:
                _check_rect = None
            crop_rect = None
        else:
            crop_rect = foreground_window_rect()
            _check_rect = crop_rect
        try:
            from ..vision_exclusions import (
                foreground_probe, user_exclusion_block)  # Phase 3 D4c: vision_exclusions stays flat in core/
            _proc, _title = foreground_probe()
            _user_blocked = user_exclusion_block(
                _proc, _title, _check_rect)
        except Exception:  # noqa: BLE001 — probe failure is not a block
            _user_blocked = None
        if _user_blocked is not None:
            from ..capture_guard import CaptureBlockedError  # Phase 3 D4c: vision hub moved to core/desktop/

            raise CaptureBlockedError(_user_blocked)
        _backend = _routed_backend()  # v5.50 (items 41/42)
        if _backend is not None:
            raw = _backend_raw_full_frame(_backend)
            if raw is not None:
                # The backend supplied the RAW full frame; the
                # pipeline below the grab — here, the E7
                # foreground crop and the geometry record — is
                # vision's and applies exactly as on the legacy
                # path. The crop is taken in PIL from the full
                # frame (the legacy body's own bounds-unknown
                # branch does the same), translated by the
                # virtual-screen origin when it is known.
                image, raw_meta = raw
                bounds = _virtual_screen_bounds()
                if bounds is not None:
                    screen_w = bounds[2] - bounds[0]
                    screen_h = bounds[3] - bounds[1]
                    origin_x, origin_y = bounds[0], bounds[1]
                elif raw_meta and raw_meta.get("screen_width") \
                        and raw_meta.get("screen_height"):
                    screen_w = int(raw_meta["screen_width"])
                    screen_h = int(raw_meta["screen_height"])
                    origin_x = origin_y = 0
                else:
                    screen_w, screen_h = int(image.width), int(image.height)
                    origin_x = origin_y = 0
                if crop_rect is not None:
                    cl, ct, cr, cb = (int(v) for v in crop_rect)
                    image = image.crop((cl - origin_x, ct - origin_y,
                                        cr - origin_x, cb - origin_y))
                    meta = {
                        "screen_width": int(screen_w),
                        "screen_height": int(screen_h),
                        "image_width": int(image.width),
                        "image_height": int(image.height),
                        "cropped": True,
                        "crop_left": cl, "crop_top": ct,
                        "crop_width": cr - cl, "crop_height": cb - ct,
                    }
                    return _w5_note_capture(image), meta
                meta = {
                    "screen_width": int(screen_w),
                    "screen_height": int(screen_h),
                    "image_width": int(image.width),
                    "image_height": int(image.height),
                    "cropped": False,
                    "crop_left": 0, "crop_top": 0,
                    "crop_width": int(image.width),
                    "crop_height": int(image.height),
                }
                return _w5_note_capture(image), meta
            # The backend cannot grab (its documented fail-soft
            # RuntimeError — _backend_raw_full_frame): fall
            # through to the legacy body below, this module's own
            # grab path (the capture owner's ImageGrab fallback,
            # the same grab the Linux backend's OS hook uses),
            # which fails honestly when it truly cannot capture.
            # R4's fall-through, kept.
        if crop_rect is not None:
            cl, ct, cr, cb = (int(v) for v in crop_rect)
            bounds = _virtual_screen_bounds()
            if bounds is not None:
                image = _grab_image(bbox=(cl, ct, cr, cb))
                screen_w = bounds[2] - bounds[0]
                screen_h = bounds[3] - bounds[1]
                origin_x, origin_y = bounds[0], bounds[1]
            else:
                # Bounds unknown: measure the true screen from a
                # full grab and crop it in PIL — the crop rectangle
                # is in the same (primary-origin) coordinates the
                # plain grab uses.
                full = _grab_image()
                screen_w, screen_h = int(full.width), int(full.height)
                origin_x = origin_y = 0
                image = full.crop((cl, ct, cr, cb))
            expected = (cr - cl, cb - ct)
            if tuple(image.size) != expected and tuple(image.size) == (
                    screen_w, screen_h):
                # The grab ignored the bbox and returned the whole
                # screen — crop it in PIL rather than ship a frame
                # whose geometry record would lie.
                image = image.crop((cl - origin_x, ct - origin_y,
                                    cr - origin_x, cb - origin_y))
            meta = {
                "screen_width": int(screen_w),
                "screen_height": int(screen_h),
                "image_width": int(image.width),
                "image_height": int(image.height),
                "cropped": True,
                "crop_left": cl, "crop_top": ct,
                "crop_width": cr - cl, "crop_height": cb - ct,
            }
            return _w5_note_capture(image), meta
        image = _grab_image()
        meta = {
            "screen_width": int(image.width),
            "screen_height": int(image.height),
            "image_width": int(image.width),
            "image_height": int(image.height),
            "cropped": False,
            "crop_left": 0, "crop_top": 0,
            "crop_width": int(image.width),
            "crop_height": int(image.height),
        }
        return _w5_note_capture(image), meta

    def capture_screen(self, max_dim: int = 1024, quality: int = 80,
                     include_cursor: bool = False,
                     mark: tuple[int, int] | None = None,
                     full_screen: bool = False,
                     marks: bool = False,
                     crop_rect: tuple[int, int, int, int] | None = None,
                     ) -> tuple[bytes, str]:
        """Captures the screen, scales down, and compresses as JPEG.

        v5.44 (E7): the capture defaults to the FOREGROUND
        window's rectangle — the same pixel budget covers one
        window at roughly 2–3x the legibility of the whole
        desktop. `full_screen=True` is the explicit whole-desktop
        request; with no window focused the capture IS the whole
        desktop. include_cursor marks the cursor's true position
        with a standard arrow glyph before scaling. mark=(x, y)
        draws a red ring with a white centre at that screen point
        before scaling, so a caller can point at one exact spot
        (with a crop, the point is translated into the crop's
        coordinates first).

        Every capture records its geometry (true screen size, sent
        image size, and the E7 crop origin/size) for
        last_capture_info() — see E10 in the class note there. The
        default max_dim is unchanged (1024); grounding callers
        pass GROUNDING_MAX_DIM.

        DISPLAY-01: crop_rect (left, top, right, bottom in true
        screen pixels) crops the desktop frame to one monitor —
        used with full_screen=True by take_screenshot's monitor
        selector. The geometry record then states the monitor's
        origin/size, and the marks table is filtered to that
        monitor. None (the default) leaves every capture exactly
        as before.

        v5.48 (programme item 21): a denylisted foreground window
        raises capture_guard.CaptureBlockedError (from
        capture_native_image, which this funnels through) BEFORE
        any pixels are grabbed — the plain look gains NO
        confirmation ask, only the denylist refusal.

        v5.49 (programme item 30): marks=True makes this a
        grounding capture — the window's interactive controls are
        enumerated into a marks table (core/desktop/uia.py), their
        numbered boxes drawn on the frame before scaling, and the
        table recorded for last_marks() / click_by_mark. A blocked
        capture raises before enumeration and leaves NO table
        behind (the tables are cleared first, below)."""
        # v5.50 R5-V: NO backend delegation at this level — not
        # for plain captures, not for marks=True. The serving
        # backend supplies the raw frame through
        # capture_native_image above (which also runs the
        # denylist gate first); everything from here down — the
        # marks enumeration + overlay, the cursor arrow, the
        # mark ring, the thumbnail, the geometry record and the
        # JPEG encode — is this module's pipeline and applies to
        # backend-grabbed frames exactly as to legacy-grabbed
        # ones. (The items-41/42 delegation to
        # backend.capture_screen bypassed all of it: no crop, no
        # cursor arrow, no marks drawing, and the geometry was
        # recorded on the backend, where this module's consumers
        # never read it.)
        if marks:
            # Cleared up front: a capture that then raises (the
            # denylist) must leave no stale table clickable.
            self._last_marks = []
            try:
                from . import uia as _uia  # Phase 3 D4b: uia hub moved to core/desktop/ (D4c: vision now in core/desktop/, .desktop segment dropped)

                _uia.clear_last_marks()
            except Exception:  # noqa: BLE001 — best effort
                pass
        screenshot, meta = self.capture_native_image(
            full_screen=full_screen)
        if crop_rect is not None:
            # DISPLAY-01: a monitor crop of the desktop frame, in
            # true screen pixels. The frame starts at the virtual
            # origin, so the rect translates by it; the rewritten
            # meta then makes every downstream step (marks
            # filtering + drawing, cursor arrow, mark ring,
            # geometry record) work in the crop's coordinates.
            # A crop outside the captured frame is a caller bug
            # and fails loud — it must never silently return a
            # neighbouring monitor's pixels.
            bounds = _virtual_screen_bounds()
            ox, oy = (bounds[0], bounds[1]) if bounds else (0, 0)
            cl, ct, cr, cb = (int(v) for v in crop_rect)
            box = (max(0, cl - ox), max(0, ct - oy),
                   min(screenshot.width, cr - ox),
                   min(screenshot.height, cb - oy))
            if box[2] <= box[0] or box[3] <= box[1]:
                raise ValueError(
                    f"monitor rectangle {(cl, ct, cr, cb)} is "
                    "outside the captured desktop frame")
            screenshot = screenshot.crop(box)
            meta = dict(meta)
            meta["cropped"] = True
            meta["crop_left"] = cl
            meta["crop_top"] = ct
            meta["crop_width"] = box[2] - box[0]
            meta["crop_height"] = box[3] - box[1]
        true_width = int(meta["screen_width"])
        true_height = int(meta["screen_height"])
        origin_x = int(meta["crop_left"]) if meta["cropped"] else 0
        origin_y = int(meta["crop_top"]) if meta["cropped"] else 0
        if marks:
            found = ground_marks_for_frame(screenshot, meta)
            if crop_rect is not None:
                # A monitor crop must not carry the foreground
                # window's marks table when that window sits on
                # another monitor: keep only marks whose true-pixel
                # rect intersects this crop, so click_by_mark can
                # never resolve an id that is not in the sent image.
                cl, ct, cr, cb = (int(v) for v in crop_rect)
                kept = []
                for m in found:
                    try:
                        ml, mt, mr, mb = (int(v) for v in m["rect"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if ml < cr and mr > cl and mt < cb and mb > ct:
                        kept.append(m)
                found = kept
            self._last_marks = [dict(m) for m in found]
            screenshot = draw_marks_on_image(
                screenshot, found, origin=(origin_x, origin_y))
        if include_cursor:
            try:
                from PIL import ImageDraw

                pos = _cursor_position()
                if pos is not None:
                    cx, cy = pos[0] - origin_x, pos[1] - origin_y
                    if screenshot.mode != "RGB":
                        screenshot = screenshot.convert("RGB")
                    draw = ImageDraw.Draw(screenshot)
                    s = max(14, screenshot.width // 64)
                    arrow = [(cx, cy), (cx, cy + s),
                             (cx + s * 0.32, cy + s * 0.72),
                             (cx + s * 0.5, cy + s * 1.05),
                             (cx + s * 0.68, cy + s * 0.95),
                             (cx + s * 0.5, cy + s * 0.62),
                             (cx + s * 0.85, cy + s * 0.62)]
                    draw.polygon(arrow, fill=(255, 255, 255),
                                 outline=(0, 0, 0))
            except Exception:  # noqa: BLE001 — a marker must
                pass  # never sink the capture itself
        if mark is not None:
            try:
                from PIL import ImageDraw

                mx, my = int(mark[0]) - origin_x, int(mark[1]) - origin_y
                if screenshot.mode != "RGB":
                    screenshot = screenshot.convert("RGB")
                draw = ImageDraw.Draw(screenshot)
                r = max(10, screenshot.width // 48)
                draw.ellipse((mx - r, my - r, mx + r, my + r),
                             outline=(255, 0, 0), width=max(3, r // 4))
                inner = max(3, r // 3)
                draw.ellipse((mx - inner, my - inner,
                              mx + inner, my + inner),
                             fill=(255, 255, 255), outline=(255, 0, 0))
            except Exception:  # noqa: BLE001 — a marker must
                pass  # never sink the capture itself
        # Scale down while maintaining aspect ratio
        screenshot.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        # Convert to RGB (in case screenshot is RGBA)
        if screenshot.mode in ("RGBA", "P"):
            screenshot = screenshot.convert("RGB")

        # Record the capture geometry (v5.43, E10 + v5.44, E7):
        # true size from before the thumbnail, sent size from the
        # image as encoded, crop origin/size from the native frame.
        self._capture_counter += 1
        self._last_capture_info = {
            "screen_width": true_width,
            "screen_height": true_height,
            "image_width": int(screenshot.width),
            "image_height": int(screenshot.height),
            "max_dim": int(max_dim),
            "cropped": bool(meta["cropped"]),
            "crop_left": int(meta["crop_left"]),
            "crop_top": int(meta["crop_top"]),
            "crop_width": int(meta["crop_width"]),
            "crop_height": int(meta["crop_height"]),
            # 2026-11-03 (norm1000 freshness): True when the crop is
            # a caller-supplied monitor rectangle (DISPLAY-01),
            # not the foreground window — the crop-grid freshness
            # check skips the foreground comparison for those.
            "monitor_crop": crop_rect is not None,
            # 2026-11-03 (norm1000 freshness): the virtual bounds
            # measured at capture time, for the live-vs-recorded
            # arrangement comparison (None when unmeasurable —
            # the check then skips the bounds branch, not refuse).
            "virtual_bounds": _record_virtual_bounds(),
            # 2026-11-05 (control-existence C7/C8): the capture
            # id for C7 capture binding (a bare-coordinate action
            # names the capture it was read from; the verifier
            # refuses when the last capture is not that one),
            # plus the foreground window identity at capture
            # time for C8 same-rectangle swap detection. Both
            # probes are guarded: unknown reads as None and the
            # freshness branches skip, never refuse, on unknown
            # identity — old and hand-built records without
            # these keys behave exactly as before.
            "capture_id": self._capture_counter,
            "foreground_hwnd": _capture_foreground_hwnd(),
            "foreground_title": _capture_foreground_title(),
        }

        # C7 crop producer (2026-11-05): retain the SENT image
        # (thumbnailed above, RGB by now) as grayscale in the
        # single slot, with its id and a copy of the record.
        # Best-effort: slot fill never sinks a capture.
        try:
            _gray = screenshot.convert("L")
            self._last_sent_frame = {
                "capture_id": self._capture_counter,
                "width": int(screenshot.width),
                "height": int(screenshot.height),
                "gray": _gray.tobytes(),
                "record": dict(self._last_capture_info),
            }
        except Exception:  # noqa: BLE001 — slot fill never sinks
            pass

        buf = io.BytesIO()
        screenshot.save(buf, format="JPEG", quality=quality)
        return buf.getvalue(), "image/jpeg"

    def capture_webcam(self, max_dim: int = 1024, quality: int = 80) -> tuple[bytes, str] | None:
        """Captures a single frame from the local webcam.

        v5.48 (programme item 21): a denylisted foreground window
        means no frame is captured at all — None, this method's
        existing "no frame" shape. (The webcam_snapshot HANDLER
        checks the guard itself before calling here, so its
        refusal names the reason; this check is the backstop for
        every other caller of this entry.)"""
        if _capture_block_reason() is not None:
            return None
        if not HAS_OPENCV:
            return None

        cap = cv2.VideoCapture(self.camera_index)
        if not cap.isOpened():
            return None

        try:
            ret, frame = cap.read()
            if not ret or frame is None:
                return None

            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb_frame)
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            return buf.getvalue(), "image/jpeg"
        finally:
            cap.release()

    # -- MediaPipe gesture/presence input (B-R1-8) ---------------------
    # Thin driver-callable hub entry to core/desktop/vision_gestures.py.
    # The module owns the channel (models, consent, bounded session);
    # these methods only hold the ONE live session slot and delegate.
    # Lazy import: vision_gestures imports numpy/PIL at top level and
    # onnxruntime lazily, so this manager never gains a hard vision
    # dependency. A missing/denied camera, missing weights, or missing
    # onnxruntime raises VisionGesturesUnavailable — never a silent
    # fallback to another input.
    def start_gesture_session(self, duration_s=10.0, frame_source=None):
        """Start ONE bounded gesture watch (default 10 s, hard cap
        30 s). Returns the session; the caller polls it and stops it.
        Raises VisionGesturesUnavailable on a guard refusal."""
        from . import vision_gestures as _vg

        if getattr(self, "_gesture_session", None) is not None:
            self.stop_gesture_session()
        session = _vg.VisionGesturesSession(
            max_duration_s=duration_s, frame_source=frame_source)
        session.start()
        self._gesture_session = session
        return session

    def poll_gesture_session(self):
        """One poll of the live gesture session (see
        VisionGesturesSession.poll). No live session is an honest
        empty reading, never a fabricated gesture."""
        session = getattr(self, "_gesture_session", None)
        if session is None:
            return {"gesture": None, "gesture_confidence": 0.0,
                    "face_present": False, "hand_present": False,
                    "handedness": None, "frames": 0, "elapsed_s": 0.0,
                    "done": True}
        return session.poll()

    def stop_gesture_session(self):
        """Close the camera and return the watch summary (or an empty
        summary when no session was live). Idempotent."""
        session = getattr(self, "_gesture_session", None)
        self._gesture_session = None
        if session is None:
            return {"gestures_seen": [], "stable_gesture": None,
                    "frames": 0, "face_frames": 0, "hand_frames": 0,
                    "face_present": False, "hand_present": False,
                    "elapsed_s": 0.0}
        return session.stop()


vision_manager = VisionManager()


def capture_geometry_line(info: dict | None = None) -> str:
    """One plain sentence stating the coordinate contract of a
    capture (v5.43, E10): the TRUE screen size and the size of the
    image that was actually sent, e.g. "Screen is 2560x1440; this
    image is 1568x882 — coordinates in the image are scaled."
    With info=None it reads the last capture's geometry; "" when
    no capture geometry is known (nothing honest can be said).

    v5.44 (E7): a foreground-window crop appends its origin and
    size — "The image shows the foreground window at (100, 50),
    800x600." — so coordinates read off a cropped image stay
    interpretable against the true screen. An uncropped capture's
    line is byte-identical to v5.43."""
    # v5.50 R5-V: NO backend delegation here. The line is computed
    # from THIS module's capture record — the pipeline records
    # every capture (backend-grabbed frames included) on
    # vision_manager, so the sentence is identical whichever grab
    # supplied the pixels. (The items-41/42 delegation read the
    # BACKEND's own record instead, so a geometry set on
    # vision_manager — the record every caller sets and reads —
    # silently produced "".)
    if info is None:
        try:
            info = vision_manager.last_capture_info()
        except Exception:  # noqa: BLE001 — geometry is best-effort
            return ""
    if not info:
        return ""
    try:
        sw, sh = int(info["screen_width"]), int(info["screen_height"])
        iw, ih = int(info["image_width"]), int(info["image_height"])
    except (KeyError, TypeError, ValueError):
        return ""
    if (sw, sh) == (iw, ih):
        line = (f"Screen is {sw}x{sh}; this image is {iw}x{ih} — "
                f"coordinates in the image match screen pixels.")
    else:
        line = (f"Screen is {sw}x{sh}; this image is {iw}x{ih} — "
                f"coordinates in the image are scaled.")
    if info.get("cropped"):
        try:
            cl, ct = int(info["crop_left"]), int(info["crop_top"])
            cw, ch = int(info["crop_width"]), int(info["crop_height"])
        except (KeyError, TypeError, ValueError):
            return line
        line += (f" The image shows the foreground window at "
                 f"({cl}, {ct}), {cw}x{ch}.")
    # v5.49 (programme item 30): cursor_control's coordinate mode
    # default flipped from "pixels" to "norm1000", and the E10
    # geometry line now states that default so coordinates read
    # off a capture are interpreted in the mode the cursor tool
    # will actually use. Legacy contract change: the line
    # previously ended at the crop sentence above — suites pinning
    # the exact pre-v5.49 line are the coordinator's to reconcile.
    line += (" Coordinates for cursor_control default to norm1000 "
             "(0-1000 across the image); pass coords='pixels' for "
             "true screen pixels.")
    return line


def marks_table_text(marks=None) -> str:
    """The brain-facing id->control table for a marks list (the
    last grounding table when none is passed): one line naming
    every id with its control name, type and true-pixel rect —
    the text that rides alongside the grounding frame so a brain
    can pick an ID for click_by_mark. "" when there are no marks
    (nothing honest to list). Desktop programme, F-06: a mark
    from the unnamed layer (source "uia_unnamed") carries an
    explicit "[unnamed control — position only, no label]"
    suffix, so the brain can tell a named control from a
    positional guess."""
    if marks is None:
        marks = vision_manager.last_marks()
    if not marks:
        return ""
    parts = []
    for mark in marks[:40]:
        try:
            left, top, right, bottom = (int(v) for v in mark["rect"])
            rect = f"({left},{top})\u2013({right},{bottom})"
        except (KeyError, TypeError, ValueError):
            rect = "unknown position"
        line = (f"[{mark.get('id')}] '{mark.get('name')}' "
                f"({mark.get('control_type')}) at {rect}")
        if mark.get("source") == "uia_unnamed":
            line += " [unnamed control — position only, no label]"
        parts.append(line)
    extra = (f" (+{len(marks) - 40} more, not shown)"
             if len(marks) > 40 else "")
    return ("Grounding marks on this frame — click one with "
            "click_by_mark using its id: " + "; ".join(parts)
            + extra + ".")


def last_capture_info() -> dict | None:
    """Module-level shortcut for vision_manager.last_capture_info()
    (v5.43, E10) — the geometry of the last screen capture."""
    return vision_manager.last_capture_info()


def last_marks() -> list:
    """Module-level shortcut for vision_manager.last_marks()
    (v5.49, programme item 30) — the last grounding capture's
    marks table."""
    return vision_manager.last_marks()


def last_sent_frame() -> dict | None:
    """Module-level shortcut for vision_manager.last_sent_frame()
    (C7 crop producer, 2026-11-05) — the single-slot retained
    sent image (grayscale thumbnail + id + record), or None."""
    return vision_manager.last_sent_frame()


def capture_native_image(full_screen: bool = False):
    """Module-level shortcut for
    vision_manager.capture_native_image() (v5.44, E2/E7) — a
    full-resolution (image, meta) frame: the foreground window by
    default, the whole desktop when full_screen or unfocused."""
    return vision_manager.capture_native_image(full_screen=full_screen)

