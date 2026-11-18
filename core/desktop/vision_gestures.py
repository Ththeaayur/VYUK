"""MediaPipe vision input channel (B-R1-8) — hand tracking + face detection.

What this is
------------
A real vision input channel: the user's hand (21 landmarks, handedness) and
face (bounding box + keypoints) from the camera, as ONNX inference over the
tree's already-declared onnxruntime. The neural nets are MediaPipe's
BlazePalm (palm detection), hand-landmarks and BlazeFace short-range models,
converted .tflite -> .onnx at build time (see
``~/workspace/code-lab/mediapipe_models/README.md``) and supplied by the
operator under ``data/models/`` — nothing is downloaded, and the
``mediapipe`` PyPI package is never imported (its Tasks APIs send
performance/utilisation metrics to Google with no documented opt-out,
which would breach the product's telemetry-OFF rule).

The pipeline is re-implemented in-tree from MediaPipe's own source, and
verified numerically against the MediaPipe 1.2.2 Tasks API on real photos
(see ``~/workspace/code-lab/mediapipe_verify/verify_pipeline.py``):
face box ~0.005, hand landmarks mean ~0.012 / max ~0.041 (wrist), world
landmarks 0.022-0.036 m, handedness correct on both hands, blank/noise
frames detect nothing. Sources for every stage:
  * anchors — ``mediapipe/calculators/tflite/ssd_anchors_calculator.cc``
    (fixed_anchor_size => unit anchors; layer 0: 2/cell, merged
    stride-16 group: 6/cell; palm 2016, face 896)
  * decode — ``mediapipe/calculators/tensor/tensors_to_detections_calculator.cc``
    (reverse_output_order => XYWH raw order; sigmoid score, clip +/-100)
  * NMS — ``mediapipe/calculators/util/non_max_suppression_calculator.cc``
    (WEIGHTED, IoU 0.3: the kept box/keypoints are the score-weighted mean
    of the overlapping group)
  * palm -> rect — ``mediapipe/modules/hand_landmark/palm_detection_detection_to_roi.pbtxt``
    (rotation from keypoint 0 wrist -> keypoint 2 middle MCP, target 90 deg;
    RectTransformation scale 2.6, shift_y -0.5, square_long)
  * landmarks — ``mediapipe/modules/hand_landmark/hand_landmark_cpu.pbtxt``
    (224x224 letterboxed rotated crop, float [0,1]; raw x,y are tensor
    pixels -> /224; z -> /224/0.4 per tensors_to_landmarks_calculator.cc)
  * face — ``mediapipe/tasks/cc/vision/face_detector/face_detector_graph.cc``
    (128x128 letterbox; legacy defaults min_scale 0.1484375, max_scale 0.75)

Loophole family — "the vision lies, or the vision is missing"
--------------------------------------------------------------
1. A gesture shown as recognised when it was not (a wave with no wave).
2. A face shown as present when the user is not at the desk.
3. A camera frame captured without consent.
4. A surface claimed as covered that has no vision input.
ONE design closes the class: ONE vision channel in which every gesture is
a real detection from the models (never a placeholder); a gesture is only
reported after it is stable across consecutive frames at confidence
>= 0.5 with palm score >= 0.5 and hand presence >= 0.5; presence is only
claimed from a detection in the CURRENT frame (never latched); every frame
is captured only after the capture guard allows the camera for this
session, inside a bounded session that stops itself; a surface without a
vision input is never shown as covered — the channel reports itself
unavailable (missing models / no camera / denied permission) instead of
falling back to another input.
Rejected alternatives (recorded, not shipped):
  * a placeholder gesture (a lie — the channel would claim a capability
    it does not have);
  * acting on a single-frame classification (a flicker is not a gesture);
  * a camera running without consent or continuously in the background
    (a privacy violation — the session is bounded and guard-gated);
  * a surface claimed as covered with no vision (a lie — the honest
    failure is the product).

Privacy contract: frames are processed and dropped — only the gesture
name, the presence boolean and landmark-derived geometry are kept, never
a frame. No frame is sent anywhere; the inference path has no network.
The camera never runs without the user's explicit per-session consent
(the capture guard), never runs continuously (the session is bounded),
and a missing/denied camera permission is an honest failure.

Licence (PROPOSED — parent rules): MediaPipe code is Apache-2.0 (read
from the repo LICENSE file itself 2026-11-09). The model files carry no
separate licence statement on their pages or inside the .tflite files;
the parent rules whether the project's Apache-2.0 covers the weights.
No new pip dependency: the runtime is the already-declared onnxruntime
1.31.0 + numpy + Pillow (all already in the tree).
"""

import math
import os
from .. import config_spec
import time
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Constants — the model contract (MediaPipe source, see module docstring)
# ---------------------------------------------------------------------------

PALM_MODEL_FILE = "mediapipe_palm.onnx"
LANDMARKS_MODEL_FILE = "mediapipe_hand_landmarks.onnx"
FACE_MODEL_FILE = "mediapipe_face.onnx"

PALM_INPUT_SIZE = 192
LANDMARKS_INPUT_SIZE = 224
FACE_INPUT_SIZE = 128

PALM_MIN_SCORE = 0.5
FACE_MIN_SCORE = 0.5
NMS_IOU_THRESHOLD = 0.3
HAND_PRESENCE_MIN = 0.5
GESTURE_MIN_CONFIDENCE = 0.5
# A gesture is only reported after this many consecutive polls agree.
STABLE_FRAMES = 3
# The session never runs longer than this; the tool's default is shorter.
MAX_WATCH_S = 30.0
DEFAULT_WATCH_S = 10.0
POLL_INTERVAL_S = 0.2

# Landmark indices (MediaPipe hand topology).
WRIST = 0
THUMB_TIP = 4
INDEX_MCP, INDEX_PIP, INDEX_TIP = 5, 6, 8
MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP = 9, 10, 12
RING_MCP, RING_PIP, RING_TIP = 13, 14, 16
PINKY_MCP, PINKY_PIP, PINKY_TIP = 17, 18, 20

# Single-frame gestures. Motion gestures (wave / swipe) are session-level.
GESTURES = ("open_palm", "fist", "pinch", "point")


class VisionGesturesUnavailable(Exception):
    """Honest failure: the vision channel cannot run (missing models,
    missing onnxruntime, no camera, or a denied permission). Never a
    silent fallback to another input."""


# ---------------------------------------------------------------------------
# Model resolution — disk only, never a download (vision_backend pattern)
# ---------------------------------------------------------------------------

def _candidate_model_dirs():
    """Where vision weights may live, in priority order: the explicit
    override, then the bundled install copy, then data/models."""
    dirs = []
    override = config_spec.env("VYUK_VISION_MODELS_DIR").strip()
    if override:
        dirs.append(Path(override))
    try:
        from ..voice.local_assets_model import MODELS_DIR, _bundled_child
    except Exception:  # noqa: BLE001 — resolution must not crash the import
        return dirs
    bundled = _bundled_child("models")
    if bundled is not None:
        dirs.append(bundled)
    dirs.append(MODELS_DIR)
    return dirs


def resolve_model_file(filename):
    """Path to an operator-supplied weights file, or None (honest
    absence — the caller reports it, never downloads it)."""
    for d in _candidate_model_dirs():
        p = d / filename
        try:
            if p.is_file() and p.stat().st_size > 0:
                return p
        except OSError:
            continue
    return None


def models_status():
    """{name: path-or-None} for the three weights — the channel's
    honest coverage statement."""
    return {
        "palm": resolve_model_file(PALM_MODEL_FILE),
        "landmarks": resolve_model_file(LANDMARKS_MODEL_FILE),
        "face": resolve_model_file(FACE_MODEL_FILE),
    }


# ---------------------------------------------------------------------------
# SSD anchors (ssd_anchors_calculator.cc GenerateAnchors)
# ---------------------------------------------------------------------------

def make_anchors(num_layers, min_scale, max_scale, input_size, strides,
                 aspect_ratios=(1.0,), fixed_anchor_size=True,
                 interpolated_aspect=1.0):
    """Anchor (x_center, y_center, w, h), normalised. With
    fixed_anchor_size every anchor is a unit square — only the centre
    and the count per cell matter, which is what makes the decode
    insensitive to the anchor order inside a cell."""
    def calc_scale(idx, n):
        if n == 1:
            return (min_scale + max_scale) * 0.5
        return min_scale + (max_scale - min_scale) * idx / (n - 1.0)

    anchors = []
    layer_id = 0
    while layer_id < num_layers:
        sizes = []
        last = layer_id
        while last < len(strides) and strides[last] == strides[layer_id]:
            scale = calc_scale(last, len(strides))
            for _ in aspect_ratios:
                sizes.append(scale)
            if interpolated_aspect > 0.0:
                nxt = 1.0 if last == len(strides) - 1 else calc_scale(
                    last + 1, len(strides))
                sizes.append(math.sqrt(scale * nxt))
            last += 1
        stride = strides[layer_id]
        fm = math.ceil(input_size / stride)
        for y in range(fm):
            for x in range(fm):
                for _ in sizes:
                    anchors.append(((x + 0.5) / fm, (y + 0.5) / fm, 1.0, 1.0))
        layer_id = last
    return anchors


PALM_ANCHORS = make_anchors(4, 0.1484375, 0.75, PALM_INPUT_SIZE,
                            [8, 16, 16, 16])
FACE_ANCHORS = make_anchors(4, 0.1484375, 0.75, FACE_INPUT_SIZE,
                            [8, 16, 16, 16])


# ---------------------------------------------------------------------------
# Image -> tensor (ImageToTensorCalculator: letterbox, float [0,1])
# ---------------------------------------------------------------------------

def letterbox_resize(img_rgb, size):
    """Resize keeping aspect ratio into a size x size black canvas.
    Returns (tensor NCHW float32 [0,1], pad_x, pad_y, resized_w,
    resized_h) — the padding is needed to map detections back."""
    W, H = img_rgb.size
    scale = min(size / W, size / H)
    rw, rh = int(round(W * scale)), int(round(H * scale))
    small = img_rgb.resize((rw, rh), Image.BILINEAR)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    px, py = (size - rw) // 2, (size - rh) // 2
    canvas.paste(small, (px, py))
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    return np.transpose(arr, (2, 0, 1))[None], px, py, rw, rh


def letterbox_remove(x_norm, y_norm, size, px, py, rw, rh):
    """DetectionLetterboxRemoval: tensor-normalised -> image-normalised."""
    return ((x_norm * size - px) / rw, (y_norm * size - py) / rh)


# ---------------------------------------------------------------------------
# Decode + NMS (tensors_to_detections + non_max_suppression, WEIGHTED)
# ---------------------------------------------------------------------------

def _sigmoid(v):
    return 1.0 / (1.0 + math.exp(-v))


def decode_detections(regressors, scores, anchors, num_coords,
                      num_keypoints, scale, min_score):
    """TensorsToDetections, XYWH raw order (reverse_output_order=true).
    regressors: (N, num_coords); scores: (N,). Returns detections with
    a normalised box (xmin, ymin, width, height), a score and keypoints
    in tensor-normalised coordinates."""
    dets = []
    for i in range(len(anchors)):
        s = _sigmoid(max(-100.0, min(100.0, float(scores[i]))))
        if s < min_score:
            continue
        raw = regressors[i]
        ax, ay, aw, ah = anchors[i]
        xc = raw[0] / scale * aw + ax
        yc = raw[1] / scale * ah + ay
        w = raw[2] / scale * aw
        h = raw[3] / scale * ah
        kps = []
        for k in range(num_keypoints):
            kx = raw[4 + 2 * k] / scale * aw + ax
            ky = raw[5 + 2 * k] / scale * ah + ay
            kps.append((kx, ky))
        dets.append({"score": s, "xmin": xc - w / 2, "ymin": yc - h / 2,
                     "width": w, "height": h, "keypoints": kps})
    return dets


def _iou(a, b):
    ax0, ay0 = a["xmin"], a["ymin"]
    ax1, ay1 = ax0 + a["width"], ay0 + a["height"]
    bx0, by0 = b["xmin"], b["ymin"]
    bx1, by1 = bx0 + b["width"], by0 + b["height"]
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    union = a["width"] * a["height"] + b["width"] * b["height"] - inter
    return inter / union if union > 0 else 0.0


def weighted_nms(dets, threshold=NMS_IOU_THRESHOLD):
    """NonMaxSuppressionCalculator, algorithm WEIGHTED: the kept
    detection's box/keypoints are the score-weighted mean of its
    overlapping group; its score stays the group's best."""
    order = sorted(range(len(dets)), key=lambda i: -dets[i]["score"])
    kept, used = [], set()
    for idx in order:
        if idx in used:
            continue
        base = dets[idx]
        group = [j for j in order
                 if j not in used and _iou(dets[j], base) > threshold]
        tot = sum(dets[j]["score"] for j in group)
        merged = dict(base)
        merged["xmin"] = sum(dets[j]["xmin"] * dets[j]["score"]
                             for j in group) / tot
        merged["ymin"] = sum(dets[j]["ymin"] * dets[j]["score"]
                             for j in group) / tot
        merged["width"] = (sum((dets[j]["xmin"] + dets[j]["width"])
                               * dets[j]["score"] for j in group) / tot
                           - merged["xmin"])
        merged["height"] = (sum((dets[j]["ymin"] + dets[j]["height"])
                                * dets[j]["score"] for j in group) / tot
                            - merged["ymin"])
        merged["keypoints"] = [
            (sum(dets[j]["keypoints"][k][0] * dets[j]["score"]
                 for j in group) / tot,
             sum(dets[j]["keypoints"][k][1] * dets[j]["score"]
                 for j in group) / tot)
            for k in range(len(base["keypoints"]))]
        kept.append(merged)
        used.update(group)
    return kept


def _remove_letterbox(dets, size, px, py, rw, rh):
    for d in dets:
        d["xmin"], d["ymin"] = letterbox_remove(
            d["xmin"], d["ymin"], size, px, py, rw, rh)
        d["width"], d["height"] = (d["width"] * size / rw,
                                   d["height"] * size / rh)
        d["keypoints"] = [letterbox_remove(kx, ky, size, px, py, rw, rh)
                          for kx, ky in d["keypoints"]]
    return dets


# ---------------------------------------------------------------------------
# Palm -> rect (DetectionsToRects + RectTransformation)
# ---------------------------------------------------------------------------

def _norm_radians(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def palm_to_rect(det, image_w, image_h):
    """PalmDetectionDetectionToRoi: rotation from keypoint 0 (wrist)
    to keypoint 2 (middle-finger MCP), target 90 deg; then
    RectTransformation scale 2.6, shift_y -0.5, square_long."""
    (x0, y0) = det["keypoints"][0]
    (x1, y1) = det["keypoints"][2]
    rot = _norm_radians(
        math.pi / 2 - math.atan2(-(y1 * image_h - y0 * image_h),
                                 x1 * image_w - x0 * image_w))
    cx = det["xmin"] + det["width"] / 2
    cy = det["ymin"] + det["height"] / 2
    w, h = det["width"], det["height"]
    # RectTransformationCalculator (normalised), shift_x=0, shift_y=-0.5.
    if rot == 0.0:
        cy += h * (-0.5)
    else:
        xs = (image_h * h * 0.5 * math.sin(rot)) / image_w
        ys = (-image_h * h * 0.5 * math.cos(rot)) / image_h
        cx, cy = cx + xs, cy + ys
    long_side = max(w * image_w, h * image_h)
    w, h = long_side / image_w, long_side / image_h
    return {"x_center": cx, "y_center": cy, "width": w * 2.6,
            "height": h * 2.6, "rotation": rot}


def rotated_crop(img_rgb, rect, size=LANDMARKS_INPUT_SIZE):
    """Crop the rotated square rect to a size x size tensor, float
    [0,1] NCHW. The rect is square in pixels (square_long), so there
    is no letterbox — the whole tensor is the hand region."""
    W, H = img_rgb.size
    S = rect["width"] * W
    cx, cy = rect["x_center"] * W, rect["y_center"] * H
    th = rect["rotation"]
    c, s = math.cos(th), math.sin(th)
    # PIL AFFINE maps output pixel -> input pixel:
    #   in = center + R(th) @ ((out + 0.5) / size - 0.5) * S
    a = S / size * c
    b = -S / size * s
    d = S / size * s
    e = S / size * c
    cc = cx + (S / (2 * size)) * (c - s) - 0.5 * S * (c - s)
    ff = cy + (S / (2 * size)) * (s + c) - 0.5 * S * (s + c)
    crop = img_rgb.transform((size, size), Image.AFFINE, (a, b, cc, d, e, ff),
                             resample=Image.BILINEAR, fillcolor=(0, 0, 0))
    arr = np.asarray(crop, dtype=np.float32) / 255.0
    return np.transpose(arr, (2, 0, 1))[None]


def project_landmarks(lm_crop, rect, image_w, image_h):
    """LandmarkProjection: crop-normalised [0,1] -> image-normalised
    [0,1]. The inverse of the crop: rotate the offset back by the
    rect rotation and scale by the rect size."""
    S = rect["width"] * image_w
    cx, cy = rect["x_center"] * image_w, rect["y_center"] * image_h
    th = rect["rotation"]
    c, s = math.cos(th), math.sin(th)
    out = []
    for (lx, ly, lz) in lm_crop:
        u, v = (lx - 0.5) * S, (ly - 0.5) * S
        out.append(((cx + u * c - v * s) / image_w,
                    (cy + u * s + v * c) / image_h, lz))
    return out


# ---------------------------------------------------------------------------
# Gesture classification from the 21 landmarks (pure geometry)
# ---------------------------------------------------------------------------

def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _pip_cos(lm, mcp, pip, tip):
    """Cosine of the angle at the PIP joint between (MCP-PIP) and
    (TIP-PIP). Straight finger: the two vectors oppose, cos -> -1.
    Curled finger: the tip folds back, cos -> +1."""
    ax, ay = lm[mcp][0] - lm[pip][0], lm[mcp][1] - lm[pip][1]
    bx, by = lm[tip][0] - lm[pip][0], lm[tip][1] - lm[pip][1]
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na == 0 or nb == 0:
        return 0.0
    return (ax * bx + ay * by) / (na * nb)


def _finger_state(lm, mcp, pip, tip):
    """'extended' (cos < -0.5), 'curled' (cos > 0.0), else 'uncertain'.
    The dead zone between them is deliberate: a half-bent finger is
    neither, and the classifier refuses to claim a gesture from it."""
    cos = _pip_cos(lm, mcp, pip, tip)
    if cos < -0.5:
        return "extended", (-cos - 0.5) / 0.5
    if cos > 0.0:
        return "curled", min(1.0, cos)
    return "uncertain", 0.0


def classify_gesture(landmarks):
    """Single-frame gesture from the 21 image-normalised landmarks
    [(x, y, z), ...]. Returns (gesture-or-None, confidence 0..1).
    A gesture is only returned at confidence >= 0.5 — below that the
    frame says nothing, it does not guess. Checked in order so a
    pinch (thumb meets an extended index) is never read as a fist
    (both fingers curled into the palm)."""
    if len(landmarks) < 21:
        return None, 0.0
    lm = [(float(p[0]), float(p[1])) for p in landmarks]
    hand_size = _dist(lm[WRIST], lm[MIDDLE_MCP])
    if hand_size <= 1e-6:
        return None, 0.0
    fingers = {}
    margins = {}
    for name, (mcp, pip, tip) in {
            "index": (INDEX_MCP, INDEX_PIP, INDEX_TIP),
            "middle": (MIDDLE_MCP, MIDDLE_PIP, MIDDLE_TIP),
            "ring": (RING_MCP, RING_PIP, RING_TIP),
            "pinky": (PINKY_MCP, PINKY_PIP, PINKY_TIP)}.items():
        fingers[name], margins[name] = _finger_state(lm, mcp, pip, tip)
    # Thumb: spread away from the index MCP = extended; folded onto
    # the palm = curled. (Distance, not an angle — the thumb's chain
    # bends in a different plane.) The thumb is a VETO for open_palm
    # (a folded thumb is not an open palm) and never the confidence
    # driver — its spread varies too much between hands to scale a
    # confidence, and the fingers are the primary signal.
    thumb_gap = _dist(lm[THUMB_TIP], lm[INDEX_MCP]) / hand_size
    thumb_curled = thumb_gap < 0.30

    # Pinch first: thumb tip meets an EXTENDED index tip.
    pinch_gap = _dist(lm[THUMB_TIP], lm[INDEX_TIP]) / hand_size
    if fingers["index"] == "extended" and pinch_gap < 0.35:
        conf = min((0.35 - pinch_gap) / 0.35, margins["index"])
        if conf >= GESTURE_MIN_CONFIDENCE:
            return "pinch", conf
    # Point: index out, the rest curled.
    if (fingers["index"] == "extended"
            and fingers["middle"] == "curled"
            and fingers["ring"] == "curled"
            and fingers["pinky"] == "curled"):
        conf = min(margins["index"], margins["middle"], margins["ring"],
                   margins["pinky"])
        if conf >= GESTURE_MIN_CONFIDENCE:
            return "point", conf
    # Open palm: everything out, thumb not folded onto the palm.
    if (all(fingers[f] == "extended"
            for f in ("index", "middle", "ring", "pinky"))
            and not thumb_curled):
        conf = min(margins[f] for f in
                   ("index", "middle", "ring", "pinky"))
        if conf >= GESTURE_MIN_CONFIDENCE:
            return "open_palm", conf
    # Fist: everything in. (The thumb is not required — the four
    # curled fingers are sufficient evidence; a folded thumb's exact
    # resting place varies too much to gate on.)
    if all(fingers[f] == "curled"
           for f in ("index", "middle", "ring", "pinky")):
        conf = min(margins[f] for f in
                   ("index", "middle", "ring", "pinky"))
        if conf >= GESTURE_MIN_CONFIDENCE:
            return "fist", conf
    return None, 0.0


# ---------------------------------------------------------------------------
# Engine — ONNX sessions over operator-supplied weights
# ---------------------------------------------------------------------------

class VisionGestures:
    """The vision channel: hand tracking + face detection from RGB
    frames. Sessions may be injected (fakes for tests); otherwise the
    three operator-supplied .onnx weights are resolved and loaded.
    Raises VisionGesturesUnavailable instead of ever substituting
    another input."""

    def __init__(self, sessions=None, model_dir=None):
        if sessions is not None:
            self._sessions = sessions
            self._model_paths = {}
        else:
            # Lazy onnxruntime import: the module imports cleanly where
            # it is absent, and the channel reports itself unavailable.
            try:
                import onnxruntime as _ort
            except ImportError:
                raise VisionGesturesUnavailable(
                    "onnxruntime is not installed in this interpreter, so "
                    "the MediaPipe vision channel cannot run.")
            paths = {}
            for key, fname in (("palm", PALM_MODEL_FILE),
                               ("landmarks", LANDMARKS_MODEL_FILE),
                               ("face", FACE_MODEL_FILE)):
                if model_dir is not None:
                    p = Path(model_dir) / fname
                    paths[key] = p if p.is_file() else None
                else:
                    paths[key] = resolve_model_file(fname)
            missing = [k for k, p in paths.items() if p is None]
            if missing:
                raise VisionGesturesUnavailable(
                    "MediaPipe vision weights missing (operator-supplied): "
                    + ", ".join(
                        f"{k} ({f})" for k, f in
                        (("palm", PALM_MODEL_FILE),
                         ("landmarks", LANDMARKS_MODEL_FILE),
                         ("face", FACE_MODEL_FILE)) if k in missing)
                    + ". Place the .onnx files under data/models/ (see "
                    "mediapipe_models/README.md) — nothing is downloaded.")
            try:
                self._sessions = {
                    k: _ort.InferenceSession(str(p),
                                             providers=["CPUExecutionProvider"])
                    for k, p in paths.items()}
            except Exception as e:  # noqa: BLE001 — honest, not a crash
                raise VisionGesturesUnavailable(
                    f"MediaPipe vision weights could not be loaded: {e}")
            self._model_paths = paths

    # -- single-frame detection (frames are processed and dropped) --
    def detect_faces(self, img_rgb):
        """[{score, xmin, ymin, width, height, keypoints}] in
        image-normalised coordinates, or [] (no face — honestly)."""
        t, px, py, rw, rh = letterbox_resize(img_rgb, FACE_INPUT_SIZE)
        reg, sc = self._sessions["face"].run(None, {"input": t})
        dets = decode_detections(reg[0], sc[0][:, 0], FACE_ANCHORS, 16, 6,
                                 float(FACE_INPUT_SIZE), FACE_MIN_SCORE)
        dets = weighted_nms(dets)
        return _remove_letterbox(dets, FACE_INPUT_SIZE, px, py, rw, rh)

    def detect_hands(self, img_rgb):
        """[{palm_score, presence, handedness, handedness_score,
        landmarks [(x, y, z) image-normalised], world_landmarks}] —
        or [] (no hand — honestly)."""
        W, H = img_rgb.size
        t, px, py, rw, rh = letterbox_resize(img_rgb, PALM_INPUT_SIZE)
        reg, sc = self._sessions["palm"].run(None, {"input_1": t})
        dets = decode_detections(reg[0], sc[0][:, 0], PALM_ANCHORS, 18, 7,
                                 float(PALM_INPUT_SIZE), PALM_MIN_SCORE)
        dets = weighted_nms(dets)
        dets = _remove_letterbox(dets, PALM_INPUT_SIZE, px, py, rw, rh)
        hands = []
        for det in dets:
            rect = palm_to_rect(det, W, H)
            crop = rotated_crop(img_rgb, rect, LANDMARKS_INPUT_SIZE)
            lm, pres, hand, world = self._sessions["landmarks"].run(
                None, {"input_1": crop})
            raw = np.asarray(lm[0], dtype=np.float64).reshape(21, 3)
            # TensorsToLandmarks: x,y are tensor pixels -> /224;
            # z -> /224/0.4.
            normed = np.stack([raw[:, 0] / LANDMARKS_INPUT_SIZE,
                               raw[:, 1] / LANDMARKS_INPUT_SIZE,
                               raw[:, 2] / LANDMARKS_INPUT_SIZE / 0.4],
                              axis=1)
            presence = float(np.asarray(pres).reshape(-1)[0])
            hand_raw = float(np.asarray(hand).reshape(-1)[0])
            if presence < HAND_PRESENCE_MIN:
                continue  # the landmarks model itself says no hand
            proj = project_landmarks([tuple(v) for v in normed],
                                     rect, W, H)
            hands.append({
                "palm_score": det["score"],
                "presence": presence,
                # The handedness head is P(right hand): < 0.5 is Left.
                "handedness": "Right" if hand_raw >= 0.5 else "Left",
                "handedness_score": hand_raw if hand_raw >= 0.5
                else 1.0 - hand_raw,
                "landmarks": proj,
                "world_landmarks": [tuple(v) for v in
                                    np.asarray(world[0]).reshape(21, 3)],
            })
        return hands

    def detect_frame(self, img_rgb):
        """(hands, faces) for one frame — the channel's single entry."""
        return self.detect_hands(img_rgb), self.detect_faces(img_rgb)


# ---------------------------------------------------------------------------
# Camera frame source (default) — cv2, lazy, honest when absent
# ---------------------------------------------------------------------------

def default_frame_source(camera_index=0):
    """Yield PIL RGB frames from the camera until exhausted. The cv2
    import is lazy (the lab has no cv2 and no camera); a missing cv2
    or a camera that will not open ends the source instead of
    raising into the caller — the session reports the short watch."""
    try:
        import cv2
    except Exception:  # noqa: BLE001 — no cv2, no camera source
        return
    cap = cv2.VideoCapture(camera_index)
    if not cap.isOpened():
        return
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                return
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            yield Image.fromarray(rgb)
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Session — bounded, consent-gated, no frame retention
# ---------------------------------------------------------------------------

class VisionGesturesSession:
    """One bounded camera watch. start() refuses honestly when the
    capture guard denies the camera (durable denial, sensitive
    window, or policy) — the camera never runs without explicit
    per-session consent. poll() processes ONE frame and drops it;
    only the gesture name, the presence booleans and landmark-derived
    geometry are kept. The session stops itself at max_duration_s
    and never runs continuously in the background."""

    def __init__(self, engine=None, max_duration_s=DEFAULT_WATCH_S,
                 frame_source=None):
        self._engine = engine
        self.max_duration_s = min(float(max_duration_s), MAX_WATCH_S)
        self._frame_source = frame_source
        self._frames = None
        self._started_at = None
        self._seen = 0
        self._gesture_run = (None, 0)  # (gesture, consecutive polls)
        self._stable_gesture = None
        self._stable_confidence = 0.0
        self._wrist_x = []  # (t, x) while a hand is present
        self._gestures_seen = []
        self._face_frames = 0
        self._hand_frames = 0
        self._stopped = False

    # -- lifecycle --
    def start(self):
        """Open the camera for this session only. Raises
        VisionGesturesUnavailable on a guard refusal (never a silent
        fallback) and on a missing engine."""
        if self._engine is None:
            try:
                self._engine = VisionGestures()
            except VisionGesturesUnavailable:
                raise
        try:
            from .. import capture_guard
            allowed, why = capture_guard.ensure_capture_allowed("camera")
        except Exception as e:  # noqa: BLE001 — a broken guard refuses
            raise VisionGesturesUnavailable(
                "Camera refused: the capture guard could not run "
                f"({e}).")
        if not allowed:
            raise VisionGesturesUnavailable(f"Camera refused: {why}")
        try:
            from .. import senses_log
            senses_log.append_event(
                "vision_gesture_session", "gesture watch started")
        except Exception:  # noqa: BLE001 — logging never blocks vision
            pass
        try:
            from .. import capture_guard as _cg
            _cg.note_capture_started("camera")
        except Exception:  # noqa: BLE001 — the indicator is best-effort
            pass
        self._frames = (self._frame_source
                        if self._frame_source is not None
                        else default_frame_source())
        if not hasattr(self._frames, "__next__"):
            self._frames = iter(self._frames)
        self._started_at = time.monotonic()
        return self

    def stop(self):
        """Close the camera and return the watch summary. Idempotent."""
        if self._stopped:
            return self.summary()
        self._stopped = True
        try:
            from .. import capture_guard as _cg
            _cg.note_capture_ended("camera")
        except Exception:  # noqa: BLE001 — the indicator is best-effort
            pass
        try:
            from .. import senses_log
            senses_log.append_event(
                "vision_gesture_session",
                "gesture watch ended",
                value=self._seen, unit="frames")
        except Exception:  # noqa: BLE001 — logging never blocks vision
            pass
        return self.summary()

    # -- polling --
    def poll(self):
        """Process one frame. Returns the current reading:
        {gesture (stable or None), gesture_confidence, face_present,
        hand_present, handedness, frames, elapsed_s, done}. A gesture
        is only reported after STABLE_FRAMES consecutive polls agree
        at confidence >= 0.5 — a flicker is never a gesture."""
        if self._stopped or self._started_at is None:
            return {"gesture": self._stable_gesture,
                    "gesture_confidence": self._stable_confidence,
                    "face_present": False, "hand_present": False,
                    "handedness": None, "frames": self._seen,
                    "elapsed_s": self.elapsed(),
                    "done": True}
        elapsed = self.elapsed()
        if elapsed >= self.max_duration_s:
            return self._finish()
        try:
            frame = next(self._frames)
        except StopIteration:
            return self._finish()
        except Exception:  # noqa: BLE001 — a dead source ends the watch
            return self._finish()
        if frame is None:
            return self._finish()
        self._seen += 1
        try:
            hands, faces = self._engine.detect_frame(frame)
        except Exception:  # noqa: BLE001 — a failed frame is skipped,
            # never reported as a detection
            hands, faces = [], []
        face_present = len(faces) > 0
        hand_present = len(hands) > 0
        if face_present:
            self._face_frames += 1
        if hand_present:
            self._hand_frames += 1
        gesture, conf = (None, 0.0)
        handedness = None
        if hands:
            hand = hands[0]
            handedness = hand["handedness"]
            gesture, conf = classify_gesture(hand["landmarks"])
            wrist = hand["landmarks"][WRIST]
            self._wrist_x.append((elapsed, wrist[0]))
            self._wrist_x = self._wrist_x[-20:]
            motion = self._motion_gesture(gesture)
            if motion is not None:
                gesture, conf = motion
        if gesture is not None and conf >= GESTURE_MIN_CONFIDENCE:
            if self._gesture_run[0] == gesture:
                self._gesture_run = (gesture, self._gesture_run[1] + 1)
            else:
                self._gesture_run = (gesture, 1)
            if self._gesture_run[1] >= STABLE_FRAMES:
                if self._stable_gesture != gesture:
                    self._stable_gesture = gesture
                    self._stable_confidence = conf
                    self._gestures_seen.append(gesture)
        else:
            self._gesture_run = (None, 0)
        return {"gesture": self._stable_gesture,
                "gesture_confidence": self._stable_confidence,
                "face_present": face_present,
                "hand_present": hand_present,
                "handedness": handedness, "frames": self._seen,
                "elapsed_s": self.elapsed(), "done": False}

    def _motion_gesture(self, gesture):
        """Wave / swipe from the wrist-x history (session-level — a
        single frame cannot show motion). Wave: the open palm's wrist
        reverses direction twice with amplitude >= 0.08. Swipe: the
        wrist travels >= 0.25 in one direction while a hand is
        present. Returns (gesture, confidence) or None."""
        pts = self._wrist_x
        if len(pts) < 4:
            return None
        xs = [x for _, x in pts]
        if gesture == "open_palm":
            reversals = 0
            prev_dir = 0
            for a, b in zip(xs, xs[1:]):
                d = 1 if b - a > 0.02 else (-1 if a - b > 0.02 else 0)
                if d and prev_dir and d != prev_dir:
                    reversals += 1
                if d:
                    prev_dir = d
            if reversals >= 2 and max(xs) - min(xs) >= 0.08:
                return ("wave", 0.8)
        travel = xs[-1] - xs[0]
        if travel >= 0.25:
            return ("swipe_right", 0.8)
        if travel <= -0.25:
            return ("swipe_left", 0.8)
        return None

    def elapsed(self):
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    def _finish(self):
        summary = self.stop()
        reading = {"gesture": self._stable_gesture,
                   "gesture_confidence": self._stable_confidence,
                   "face_present": False, "hand_present": False,
                   "handedness": None, "frames": self._seen,
                   "elapsed_s": self.elapsed(), "done": True}
        reading["summary"] = summary
        return reading

    def summary(self):
        """The watch summary — gesture names and presence only, never
        a frame."""
        return {
            "gestures_seen": list(self._gestures_seen),
            "stable_gesture": self._stable_gesture,
            "frames": self._seen,
            "face_frames": self._face_frames,
            "hand_frames": self._hand_frames,
            "face_present": self._face_frames > 0,
            "hand_present": self._hand_frames > 0,
            "elapsed_s": round(self.elapsed(), 2),
        }
