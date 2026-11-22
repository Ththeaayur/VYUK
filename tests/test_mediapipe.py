"""B-R1-8 — MediaPipe vision channel (tests/test_mediapipe.py).

The loophole family (see core/desktop/vision_gestures.py): "the vision
lies, or the vision is missing" — a gesture shown as recognised when
it was not; a face shown as present when the user is not at the desk;
a camera frame captured without consent; a surface claimed as covered
with no vision. ONE design: one vision channel — every gesture is a
real MediaPipe detection (never a placeholder); every detection is
verified before it is acted on (a gesture is only reported after
STABLE_FRAMES consecutive polls agree at confidence >= 0.5); every
frame is captured only with explicit per-session consent (the capture
guard); a surface without vision is never shown as covered.

Fakes only: a fake frame source, fake ONNX sessions, fake landmarks,
a stubbed capture guard. No real camera, no real mic, no real audio.
The real-Windows leg (the real camera on his machine, judged by his
eye) is a field leg, owed, not this suite.
"""

import unittest
from unittest import mock

import numpy as np
from PIL import Image

from core import capture_guard
from core.desktop import vision_gestures as vg
from core.desktop.vision_gestures import (
    VisionGestures,
    VisionGesturesSession,
    VisionGesturesUnavailable,
    classify_gesture,
    decode_detections,
    letterbox_remove,
    letterbox_resize,
    make_anchors,
    weighted_nms,
)
from core.desktop.vision_manager import vision_manager
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_registry import tools as _registry


# ---------------------------------------------------------------------------
# Synthetic 21-landmark hands (MediaPipe topology; see
# core/desktop/vision_gestures.py for the index constants).
# A finger is EXTENDED when its MCP/PIP/TIP are collinear with the PIP
# between MCP and TIP (PIP cos -> -1); CURLED when the tip folds back
# toward the MCP (PIP cos -> +1); UNCERTAIN when the tip sits at 90
# degrees to the finger (PIP cos -> 0, the dead zone).
# ---------------------------------------------------------------------------

def _landmarks(states, thumb_xy):
    """states: finger -> 'extended' | 'curled' | 'uncertain'.
    thumb_xy: (x, y) of the thumb tip. Returns 21 (x, y, z) points."""
    lm = [(0.0, 0.0, 0.0)] * 21
    lm[0] = (0.50, 0.80, 0.0)  # wrist
    lm[1] = (0.38, 0.70, 0.0)
    lm[2] = (0.34, 0.65, 0.0)
    lm[3] = (0.32, 0.60, 0.0)
    lm[4] = (thumb_xy[0], thumb_xy[1], 0.0)  # thumb tip
    xs = {"index": 0.42, "middle": 0.50, "ring": 0.58, "pinky": 0.66}
    idx = {"index": (5, 6, 7, 8), "middle": (9, 10, 11, 12),
           "ring": (13, 14, 15, 16), "pinky": (17, 18, 19, 20)}
    for name, x in xs.items():
        mcp, pip, dip, tip = idx[name]
        state = states[name]
        lm[mcp] = (x, 0.60, 0.0)
        lm[pip] = (x, 0.50, 0.0)
        if state == "extended":
            lm[dip] = (x, 0.45, 0.0)
            lm[tip] = (x, 0.40, 0.0)
        elif state == "curled":
            lm[dip] = (x, 0.53, 0.0)
            lm[tip] = (x, 0.56, 0.0)
        else:  # uncertain: tip at 90 degrees to the finger
            lm[dip] = (x + 0.03, 0.50, 0.0)
            lm[tip] = (x + 0.06, 0.50, 0.0)
    return lm


def _open_palm(thumb_xy=(0.30, 0.55)):
    return _landmarks({f: "extended" for f in
                       ("index", "middle", "ring", "pinky")}, thumb_xy)


def _fist():
    return _landmarks({f: "curled" for f in
                       ("index", "middle", "ring", "pinky")}, (0.45, 0.65))


def _pinch():
    # Index extended, thumb tip meeting the index tip.
    return _landmarks({"index": "extended", "middle": "curled",
                       "ring": "curled", "pinky": "curled"},
                      (0.44, 0.42))


def _point():
    return _landmarks({"index": "extended", "middle": "curled",
                       "ring": "curled", "pinky": "curled"},
                      (0.30, 0.55))


def _shift(lm, dx):
    return [(x + dx, y, z) for (x, y, z) in lm]


def _hand(landmarks, handedness="Left"):
    return {"palm_score": 0.9, "presence": 0.9,
            "handedness": handedness, "handedness_score": 0.9,
            "landmarks": landmarks,
            "world_landmarks": [(0.0, 0.0, 0.0)] * 21}


def _face(score=0.9):
    return {"score": score, "xmin": 0.3, "ymin": 0.3,
            "width": 0.4, "height": 0.4, "keypoints": []}


class _FakeEngine:
    """A vision engine returning canned detections (never a real
    model). The session still runs the REAL classify_gesture over the
    landmarks, so the reported gesture is a real classification."""

    def __init__(self, hands=None, faces=None):
        self._hands = hands or []
        self._faces = faces or []
        self.calls = 0

    def detect_frame(self, frame):
        self.calls += 1
        return list(self._hands), list(self._faces)


class _FakeSession:
    """A fake ONNX InferenceSession: run() returns canned arrays."""

    def __init__(self, outputs):
        self._outputs = outputs

    def run(self, _names, _inputs):
        return self._outputs


def _frames(n):
    return [Image.new("RGB", (64, 48), (10, 20, 30)) for _ in range(n)]


def _stub_guard(test, allowed, why="test"):
    """Stub the capture guard + the capture indicators + senses log
    for one test (restored automatically)."""
    from core import senses_log
    patches = [
        mock.patch.object(capture_guard, "ensure_capture_allowed",
                          return_value=(allowed, why)),
        mock.patch.object(capture_guard, "note_capture_started",
                          lambda kind: None),
        mock.patch.object(capture_guard, "note_capture_ended",
                          lambda kind: None),
        mock.patch.object(senses_log, "append_event",
                          lambda *a, **k: None),
    ]
    for p in patches:
        test.addCleanup(p.stop)
        p.start()


# ---------------------------------------------------------------------------
# 1. The classifier: every gesture is real landmark geometry.
# ---------------------------------------------------------------------------

class TestClassifyGesture(unittest.TestCase):
    def test_open_palm_is_a_real_detection(self):
        gesture, conf = classify_gesture(_open_palm())
        self.assertEqual(gesture, "open_palm")
        self.assertGreaterEqual(conf, 0.5)

    def test_fist_is_a_real_detection(self):
        gesture, conf = classify_gesture(_fist())
        self.assertEqual(gesture, "fist")
        self.assertGreaterEqual(conf, 0.5)

    def test_pinch_is_a_real_detection(self):
        gesture, conf = classify_gesture(_pinch())
        self.assertEqual(gesture, "pinch")
        self.assertGreaterEqual(conf, 0.5)

    def test_point_is_a_real_detection(self):
        gesture, conf = classify_gesture(_point())
        self.assertEqual(gesture, "point")
        self.assertGreaterEqual(conf, 0.5)

    def test_half_bent_finger_is_uncertain_not_a_gesture(self):
        # A half-bent middle finger sits in the dead zone: the frame
        # says nothing, it does not guess.
        lm = _landmarks({"index": "extended", "middle": "uncertain",
                         "ring": "curled", "pinky": "curled"},
                        (0.30, 0.55))
        self.assertEqual(classify_gesture(lm), (None, 0.0))

    def test_folded_thumb_vetoes_open_palm(self):
        # All four fingers out, but the thumb folded onto the palm:
        # not an open palm, and not a pinch/point/fist either.
        lm = _open_palm(thumb_xy=(0.45, 0.65))
        self.assertEqual(classify_gesture(lm), (None, 0.0))

    def test_degenerate_landmarks_are_not_a_gesture(self):
        self.assertEqual(classify_gesture([(0.5, 0.5, 0.0)] * 10),
                         (None, 0.0))
        # Zero hand size (wrist on the middle MCP) is not a hand.
        lm = _open_palm()
        lm[0] = lm[9]
        self.assertEqual(classify_gesture(lm), (None, 0.0))


# ---------------------------------------------------------------------------
# 2. The pure pipeline: anchors, letterbox, decode, NMS.
# ---------------------------------------------------------------------------

class TestPipelinePure(unittest.TestCase):
    def test_anchor_counts_match_the_models(self):
        self.assertEqual(len(vg.PALM_ANCHORS), 2016)
        self.assertEqual(len(vg.FACE_ANCHORS), 896)

    def test_letterbox_resize_keeps_aspect(self):
        img = Image.new("RGB", (64, 48), (255, 0, 0))
        t, px, py, rw, rh = letterbox_resize(img, 128)
        self.assertEqual(t.shape, (1, 3, 128, 128))
        self.assertEqual((rw, rh), (128, 96))
        self.assertEqual((px, py), (0, 16))
        # A square image has no padding at all.
        t2, px2, py2, rw2, rh2 = letterbox_resize(
            Image.new("RGB", (64, 64)), 128)
        self.assertEqual((px2, py2, rw2, rh2), (0, 0, 128, 128))

    def test_letterbox_remove_roundtrips(self):
        img = Image.new("RGB", (64, 48))
        _t, px, py, rw, rh = letterbox_resize(img, 128)
        # The image centre maps back to itself.
        x, y = letterbox_remove(0.5, 0.5, 128, px, py, rw, rh)
        self.assertAlmostEqual(x, 0.5, places=5)
        self.assertAlmostEqual(y, 0.5, places=5)

    def test_decode_detections_threshold(self):
        anchors = make_anchors(4, 0.1484375, 0.75, 128,
                               [8, 16, 16, 16])
        n = len(anchors)
        reg = np.zeros((n, 16))
        reg[0] = [0, 0, 64, 64] + [0] * 12
        scores = np.full(n, -10.0)
        scores[0] = 10.0  # sigmoid(10) ~ 1.0
        dets = decode_detections(reg, scores, anchors, 16, 6, 128.0, 0.5)
        self.assertEqual(len(dets), 1)
        self.assertGreaterEqual(dets[0]["score"], 0.5)
        self.assertEqual(len(dets[0]["keypoints"]), 6)
        # All scores low: no detection, honestly.
        scores[:] = -10.0
        self.assertEqual(
            decode_detections(reg, scores, anchors, 16, 6, 128.0, 0.5),
            [])

    def test_weighted_nms_merges_overlapping_keeps_separate(self):
        a = {"score": 0.9, "xmin": 0.1, "ymin": 0.1, "width": 0.4,
             "height": 0.4, "keypoints": [(0.3, 0.3)]}
        b = {"score": 0.8, "xmin": 0.12, "ymin": 0.12, "width": 0.4,
             "height": 0.4, "keypoints": [(0.32, 0.32)]}
        c = {"score": 0.7, "xmin": 0.7, "ymin": 0.7, "width": 0.2,
             "height": 0.2, "keypoints": [(0.8, 0.8)]}
        kept = weighted_nms([a, b, c])
        self.assertEqual(len(kept), 2)
        self.assertEqual(kept[0]["score"], 0.9)  # best score survives
        self.assertEqual(len(weighted_nms([a, b])), 1)


# ---------------------------------------------------------------------------
# 3. The engine: fake ONNX sessions, real decode/classify.
# ---------------------------------------------------------------------------

class TestVisionGesturesEngine(unittest.TestCase):
    def test_engine_unavailable_without_runtime_or_weights(self):
        # The lab has no onnxruntime on system python3 and no
        # operator-supplied weights on either interpreter: the engine
        # refuses honestly instead of substituting another input.
        with self.assertRaises(VisionGesturesUnavailable):
            VisionGestures()

    def test_detect_faces_fake_session_finds_a_face(self):
        n = len(vg.FACE_ANCHORS)
        reg = np.zeros((1, n, 16))
        reg[0, 0] = [0, 0, 64, 64] + [0] * 12
        sc = np.full((1, n, 1), -10.0)
        sc[0, 0, 0] = 10.0
        eng = VisionGestures(
            sessions={"face": _FakeSession((reg, sc)),
                      "palm": _FakeSession(
                          (np.zeros((1, 2016, 18)),
                           np.full((1, 2016, 1), -10.0))),
                      "landmarks": _FakeSession(
                          (np.zeros((1, 63)), np.zeros((1, 1)),
                           np.zeros((1, 1)), np.zeros((1, 63))))})
        faces = eng.detect_faces(Image.new("RGB", (64, 64)))
        self.assertEqual(len(faces), 1)
        self.assertGreaterEqual(faces[0]["score"], 0.5)

    def test_detect_frame_fake_sessions_no_detection_is_honest_empty(self):
        eng = VisionGestures(
            sessions={"face": _FakeSession(
                (np.zeros((1, 896, 16)), np.full((1, 896, 1), -10.0))),
                      "palm": _FakeSession(
                          (np.zeros((1, 2016, 18)),
                           np.full((1, 2016, 1), -10.0))),
                      "landmarks": _FakeSession(
                          (np.zeros((1, 63)), np.zeros((1, 1)),
                           np.zeros((1, 1)), np.zeros((1, 63))))})
        hands, faces = eng.detect_frame(Image.new("RGB", (64, 64)))
        self.assertEqual((hands, faces), ([], []))

    def test_detect_hands_fake_sessions_open_palm(self):
        # Palm detection with keypoints 0/2 straight up (rotation 0)
        # on a square image, so the crop->image projection is a
        # uniform scale: the open-palm geometry survives it.
        n = len(vg.PALM_ANCHORS)
        reg = np.zeros((1, n, 18))
        # anchor 0 centre is (0.5/24, 0.5/24); raw = (target - centre)*192
        reg[0, 0] = [0, 0, 96, 96,
                     92.0, 111.2, 0, 0, 92.0, 72.8, 0, 0, 0, 0, 0, 0, 0, 0]
        sc = np.full((1, n, 1), -10.0)
        sc[0, 0, 0] = 10.0
        raw = np.array([c * 224.0 for p in _open_palm() for c in p],
                       dtype=np.float64).reshape(1, 63)
        eng = VisionGestures(
            sessions={"palm": _FakeSession((reg, sc)),
                      "landmarks": _FakeSession(
                          (raw, np.array([[0.9]]), np.array([[0.9]]),
                           np.zeros((1, 63)))),
                      "face": _FakeSession(
                          (np.zeros((1, 896, 16)),
                           np.full((1, 896, 1), -10.0)))})
        hands = eng.detect_hands(Image.new("RGB", (64, 64)))
        self.assertEqual(len(hands), 1)
        self.assertEqual(hands[0]["handedness"], "Right")
        self.assertGreaterEqual(hands[0]["presence"], 0.5)
        gesture, conf = classify_gesture(hands[0]["landmarks"])
        self.assertEqual(gesture, "open_palm")
        self.assertGreaterEqual(conf, 0.5)

    def test_detect_hands_presence_below_threshold_is_no_hand(self):
        # The landmarks model itself says no hand (presence < 0.5):
        # the palm detection is dropped, honestly.
        n = len(vg.PALM_ANCHORS)
        reg = np.zeros((1, n, 18))
        reg[0, 0] = [0, 0, 96, 96] + [0] * 14
        sc = np.full((1, n, 1), -10.0)
        sc[0, 0, 0] = 10.0
        raw = np.array([c * 224.0 for p in _open_palm() for c in p],
                       dtype=np.float64).reshape(1, 63)
        eng = VisionGestures(
            sessions={"palm": _FakeSession((reg, sc)),
                      "landmarks": _FakeSession(
                          (raw, np.array([[0.2]]), np.array([[0.9]]),
                           np.zeros((1, 63)))),
                      "face": _FakeSession(
                          (np.zeros((1, 896, 16)),
                           np.full((1, 896, 1), -10.0)))})
        self.assertEqual(eng.detect_hands(Image.new("RGB", (64, 64))), [])


# ---------------------------------------------------------------------------
# 4. The session: verification before action, consent, boundedness.
# ---------------------------------------------------------------------------

class TestVisionGesturesSession(unittest.TestCase):
    def _session(self, engine, frames, duration_s=10.0):
        return VisionGesturesSession(engine=engine,
                                     max_duration_s=duration_s,
                                     frame_source=iter(frames))

    def test_gesture_reported_only_after_stable_frames(self):
        _stub_guard(self, True)
        eng = _FakeEngine(hands=[_hand(_open_palm())])
        sess = self._session(eng, _frames(5)).start()
        self.assertIsNone(sess.poll()["gesture"])  # 1st poll: run 1
        self.assertIsNone(sess.poll()["gesture"])  # 2nd poll: run 2
        reading = sess.poll()  # 3rd poll: run 3 -> stable
        self.assertEqual(reading["gesture"], "open_palm")
        self.assertGreaterEqual(reading["gesture_confidence"], 0.5)
        sess.stop()

    def test_flicker_is_never_reported(self):
        _stub_guard(self, True)
        # Alternating gestures never agree 3 polls in a row.
        eng = _FakeEngine()
        sess = self._session(eng, _frames(6)).start()
        for i in range(6):
            eng._hands = [_hand(_open_palm() if i % 2 == 0 else _fist())]
            self.assertIsNone(sess.poll()["gesture"])
        sess.stop()

    def test_nonconfirming_frame_resets_the_run(self):
        _stub_guard(self, True)
        eng = _FakeEngine(hands=[_hand(_open_palm())])
        sess = self._session(eng, _frames(6)).start()
        sess.poll()
        sess.poll()  # run 2
        # One uncertain frame: the run resets.
        eng._hands = [_hand(_landmarks(
            {"index": "extended", "middle": "uncertain",
             "ring": "curled", "pinky": "curled"}, (0.30, 0.55)))]
        self.assertIsNone(sess.poll()["gesture"])
        eng._hands = [_hand(_open_palm())]
        sess.poll()  # run 1 again
        self.assertIsNone(sess.poll()["gesture"])  # run 2, still not 3
        sess.stop()

    def test_face_presence_only_from_a_detection(self):
        _stub_guard(self, True)
        eng = _FakeEngine(faces=[_face()])
        sess = self._session(eng, _frames(4)).start()
        self.assertTrue(sess.poll()["face_present"])
        self.assertTrue(sess.poll()["face_present"])
        eng._faces = []
        self.assertFalse(sess.poll()["face_present"])
        self.assertFalse(sess.poll()["hand_present"])
        summary = sess.stop()
        self.assertTrue(summary["face_present"])  # seen in 2 of 4
        self.assertEqual(summary["face_frames"], 2)
        self.assertFalse(summary["hand_present"])

    def test_wave_from_wrist_reversals(self):
        _stub_guard(self, True)
        eng = _FakeEngine()
        sess = self._session(eng, _frames(8)).start()
        for i, dx in enumerate([-0.20, 0.0, -0.20, 0.0, -0.20, 0.0,
                                -0.20, 0.0]):
            eng._hands = [_hand(_shift(_open_palm(), dx))]
            reading = sess.poll()
        self.assertEqual(reading["gesture"], "wave")
        sess.stop()

    def test_swipe_from_wrist_travel(self):
        _stub_guard(self, True)
        eng = _FakeEngine()
        sess = self._session(eng, _frames(8)).start()
        for i, dx in enumerate([0.0, 0.10, 0.20, 0.30, 0.40, 0.50,
                                0.60, 0.70]):
            eng._hands = [_hand(_shift(_open_palm(), dx))]
            reading = sess.poll()
        self.assertEqual(reading["gesture"], "swipe_right")
        sess.stop()

    def test_session_without_consent_is_an_honest_failure(self):
        _stub_guard(self, False, "denied by test")
        eng = _FakeEngine(hands=[_hand(_open_palm())])
        sess = self._session(eng, _frames(3))
        with self.assertRaises(VisionGesturesUnavailable) as ctx:
            sess.start()
        self.assertIn("denied by test", str(ctx.exception))
        # The camera never ran: no frame was ever processed.
        self.assertEqual(eng.calls, 0)

    def test_session_with_no_frames_is_honest_empty(self):
        _stub_guard(self, True)
        eng = _FakeEngine(hands=[_hand(_open_palm())])
        sess = self._session(eng, []).start()
        reading = sess.poll()
        self.assertTrue(reading["done"])
        self.assertEqual(reading["frames"], 0)
        self.assertEqual(reading["summary"]["gestures_seen"], [])

    def test_session_stops_at_max_duration(self):
        _stub_guard(self, True)
        eng = _FakeEngine(hands=[_hand(_open_palm())])
        sess = self._session(eng, _frames(50), duration_s=0.0).start()
        self.assertTrue(sess.poll()["done"])

    def test_stop_is_idempotent(self):
        _stub_guard(self, True)
        eng = _FakeEngine(hands=[_hand(_open_palm())])
        sess = self._session(eng, _frames(3)).start()
        sess.poll()
        first = sess.stop()
        second = sess.stop()
        self.assertEqual(first, second)
        self.assertEqual(first["frames"], 1)

    def test_summary_never_carries_a_frame(self):
        _stub_guard(self, True)
        eng = _FakeEngine(hands=[_hand(_open_palm())], faces=[_face()])
        sess = self._session(eng, _frames(4)).start()
        for _ in range(4):
            sess.poll()
        summary = sess.stop()
        self.assertEqual(set(summary),
                         {"gestures_seen", "stable_gesture", "frames",
                          "face_frames", "hand_frames", "face_present",
                          "hand_present", "elapsed_s"})


# ---------------------------------------------------------------------------
# 5. The VisionManager hub: one live session, honest empties.
# ---------------------------------------------------------------------------

class TestVisionManagerHub(unittest.TestCase):
    def setUp(self):
        vision_manager.stop_gesture_session()

    def tearDown(self):
        vision_manager.stop_gesture_session()

    def test_poll_and_stop_with_no_session_are_honest_empty(self):
        reading = vision_manager.poll_gesture_session()
        self.assertEqual(reading["gesture"], None)
        self.assertTrue(reading["done"])
        self.assertEqual(reading["frames"], 0)
        summary = vision_manager.stop_gesture_session()
        self.assertEqual(summary["frames"], 0)
        self.assertEqual(summary["gestures_seen"], [])

    def test_start_refuses_without_a_working_engine(self):
        _stub_guard(self, True)
        # Lab: no onnxruntime on system python3, no weights on Qt.
        with self.assertRaises(VisionGesturesUnavailable):
            vision_manager.start_gesture_session(duration_s=1.0)

    def test_start_refuses_when_the_guard_denies(self):
        _stub_guard(self, False, "denied by test")
        fake = _FakeEngine(hands=[_hand(_open_palm())])
        with mock.patch.object(vg, "VisionGestures",
                              lambda *a, **k: fake):
            with self.assertRaises(VisionGesturesUnavailable) as ctx:
                vision_manager.start_gesture_session(
                    duration_s=1.0, frame_source=iter(_frames(3)))
        self.assertIn("denied by test", str(ctx.exception))
        self.assertEqual(fake.calls, 0)


# ---------------------------------------------------------------------------
# 6. The watch_gestures tool: registration, gate, honest reporting.
# ---------------------------------------------------------------------------

class TestWatchGesturesTool(unittest.TestCase):
    def setUp(self):
        vision_manager.stop_gesture_session()

    def tearDown(self):
        vision_manager.stop_gesture_session()

    def test_tool_is_registered_with_a_number_duration(self):
        names = [d["name"] for d in _registry.get_declarations()]
        self.assertIn("watch_gestures", names)
        decl = next(d for d in _registry.get_declarations()
                    if d["name"] == "watch_gestures")
        self.assertEqual(
            decl["parameters"]["properties"]["duration_s"]["type"],
            "number")
        self.assertEqual(len(names), 769)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_tool_is_capture_gated_not_read(self):
        self.assertEqual(_FREE_BY_DESIGN.get("watch_gestures"), "capture")
        self.assertNotIn("watch_gestures", _READ_ONLY_TOOLS)
        self.assertNotIn("watch_gestures", _CONFIRM_GATE_TOOLS)

    def test_tool_refuses_when_the_guard_denies(self):
        _stub_guard(self, False, "denied by test")
        fake = _FakeEngine(hands=[_hand(_open_palm())])
        from core.desktop.control_plus_files import watch_gestures
        with mock.patch.object(vg, "VisionGestures",
                              lambda *a, **k: fake):
            out = watch_gestures(duration_s=2.0)
        self.assertIn("refused", out)
        self.assertIn("denied by test", out)
        self.assertEqual(fake.calls, 0)

    def test_tool_reports_what_was_actually_seen(self):
        _stub_guard(self, True)
        fake = _FakeEngine(hands=[_hand(_open_palm())], faces=[_face()])
        from core.desktop.control_plus_files import watch_gestures
        with mock.patch.object(vg, "VisionGestures",
                              lambda *a, **k: fake), \
                mock.patch.object(vg, "default_frame_source",
                                  lambda camera_index=0: iter(_frames(5))):
            out = watch_gestures(duration_s=2.0)
        self.assertIn("open_palm", out)
        self.assertIn("face present in 5 of 5 frames", out)
        self.assertIn("hand present in 5 of 5 frames", out)
        self.assertNotIn("saw nothing", out)

    def test_tool_with_no_frames_says_it_saw_nothing(self):
        _stub_guard(self, True)
        fake = _FakeEngine()
        from core.desktop.control_plus_files import watch_gestures
        with mock.patch.object(vg, "VisionGestures",
                              lambda *a, **k: fake), \
                mock.patch.object(vg, "default_frame_source",
                                  lambda camera_index=0: iter([])):
            out = watch_gestures(duration_s=1.0)
        self.assertIn("saw nothing", out)
        self.assertIn("Nothing was recorded", out)

    def test_tool_duration_is_clamped_to_1_and_30(self):
        from core.desktop.control_plus_files import watch_gestures
        seen = {}

        def fake_start(duration_s=10.0, frame_source=None):
            seen["duration_s"] = duration_s
            raise VisionGesturesUnavailable("test")

        with mock.patch.object(vision_manager, "start_gesture_session",
                              fake_start):
            watch_gestures(duration_s=999.0)
            self.assertEqual(seen["duration_s"], 30.0)
            watch_gestures(duration_s=0.1)
            self.assertEqual(seen["duration_s"], 1.0)


if __name__ == "__main__":
    unittest.main()
