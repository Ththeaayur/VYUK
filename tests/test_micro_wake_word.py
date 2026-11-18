"""R7-6 micro-wake-word: train-your-own wake word, own code.

Covers the feature extractor (determinism, shape, resampling),
the tiny numpy model (train/save/load/tamper refusal), the
streaming scorer (fires on a positive, silent on a negative,
refractory), the dataset builder (bars, WAV leg, Kokoro leg
honest refusal when the voice is not provisioned), the four
funnel tools (train via WAV files, enable consent gate, list,
status), and the standing rules: wake stays OFF, no TensorFlow /
torch / onnxruntime anywhere in the shipped path.
"""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

try:
    from core.voice import wake_features as _feat
    from core.voice import wake_model as _model
    from core.voice import wake_synth as _synth
    from core.voice import wake_training as _training
    from core.voice import wake_word as _wake_word
except Exception as exc:  # noqa: BLE001
    raise unittest.SkipTest(f"wake modules unavailable: {exc}")

_SR = 16000


def _tone_clip(freqs, *, seed, dur=0.9, noise=0.01):
    """Deterministic pseudo-speech: sequential tone bursts with a
    pitch contour. Positives and negatives differ in contour, so
    the classes are separable by construction — the test proves
    the pipeline learns, not that tones are speech."""
    rng = np.random.default_rng(seed)
    n = int(dur * _SR)
    t = np.arange(n) / _SR
    out = np.zeros(n)
    seg = n // max(len(freqs), 1)
    for i, f in enumerate(freqs):
        jitter = 1.0 + rng.uniform(-0.05, 0.05)
        s, e = i * seg, min(n, (i + 1) * seg)
        env = np.hanning(e - s)
        out[s:e] += 0.5 * env * np.sin(2 * np.pi * f * jitter * t[s:e])
    out += rng.normal(0, noise, n)
    return np.clip(out * 32767, -32768, 32767).astype(np.int16)


_POS_FREQS = (300.0, 450.0, 600.0)
_NEG_FREQ_SETS = ((200.0, 800.0, 250.0), (500.0,), (700.0, 300.0),
                  (900.0, 900.0, 900.0))


def _pos_clip(i):
    return _tone_clip(_POS_FREQS, seed=1000 + i)


def _neg_clip(i):
    if i % 4 == 3:
        rng = np.random.default_rng(2000 + i)
        return (rng.normal(0, 0.02, _SR) * 32767).astype(np.int16)
    return _tone_clip(_NEG_FREQ_SETS[i % 3], seed=2000 + i, dur=0.9)


class TestWakeFeatures(unittest.TestCase):
    def test_log_mel_shape_and_determinism(self):
        clip = _pos_clip(0)
        first = _feat.log_mel(clip)
        second = _feat.log_mel(clip)
        self.assertEqual(first.shape[1], _feat.N_MELS)
        self.assertTrue(np.array_equal(first, second),
                        "features must be deterministic")
        self.assertTrue(np.isfinite(first).all())

    def test_fixed_window_shape(self):
        clip = _pos_clip(1)
        vec = _feat.clip_to_vector(clip)
        self.assertEqual(vec.shape, (_feat.FEATURE_DIM,))
        self.assertEqual(vec.dtype, np.float32)

    def test_resample_44k_to_16k(self):
        rng = np.random.default_rng(5)
        pcm44 = (rng.normal(0, 0.3, 44100) * 32767).astype(np.int16)
        out = _feat.resample_to_16k(pcm44, 44100)
        self.assertEqual(len(out), 16000)
        # 16 kHz input passes through untouched.
        pcm16 = np.arange(100, dtype=np.int16)
        self.assertTrue(np.array_equal(_feat.resample_to_16k(pcm16, 16000),
                                       pcm16))

    def test_wav_round_trip(self):
        clip = _pos_clip(2)
        with tempfile.TemporaryDirectory() as tmp:
            path = _feat.write_wav_16k(Path(tmp) / "clip.wav", clip)
            back = _feat.read_wav_16k(path)
        self.assertEqual(len(back), len(clip))
        self.assertTrue(np.array_equal(back, clip))

    def test_read_wav_refuses_missing(self):
        with self.assertRaises(ValueError):
            _feat.read_wav_16k(Path("/nonexistent/nope.wav"))


class TestWakeModel(unittest.TestCase):
    def _dataset(self, n_pos=16, n_neg=24):
        pos = np.stack([_feat.clip_to_vector(_pos_clip(i))
                        for i in range(n_pos)])
        neg = np.stack([_feat.clip_to_vector(_neg_clip(i))
                        for i in range(n_neg)])
        x = np.concatenate([pos, neg])
        y = np.concatenate([np.ones(n_pos), np.zeros(n_neg)]).astype(np.float32)
        return x, y

    def test_train_learns_and_holds_out(self):
        x, y = self._dataset()
        rng = np.random.default_rng(7)
        order = rng.permutation(len(x))
        cut = int(len(x) * 0.8)
        train_x, train_y = x[order[:cut]], y[order[:cut]]
        held_x, held_y = x[order[cut:]], y[order[cut:]]
        model = _model.WakeModel()
        result = model.train(train_x, train_y, epochs=60)
        self.assertGreaterEqual(result["train_accuracy"], 0.9)
        self.assertGreaterEqual(model.accuracy(held_x, held_y), 0.75)

    def test_save_load_round_trip_and_tamper_refusal(self):
        x, y = self._dataset(n_pos=8, n_neg=12)
        model = _model.WakeModel()
        model.train(x, y, epochs=20)
        with tempfile.TemporaryDirectory() as tmp:
            card = _model.WakeModelCard(
                phrase="computer", slug="computer", threshold=0.7,
                n_positive=8, n_negative=12, epochs=20,
                provenance="wav-files")
            model_path, card_path = model.save(Path(tmp), card)
            self.assertLess(model_path.stat().st_size, 1024 * 1024,
                            "the tiny model must stay tiny (< 1 MB)")
            loaded = _model.WakeModel.load(model_path)
            self.assertTrue(np.allclose(
                loaded.predict_proba(x), model.predict_proba(x)))
            # Tamper: flip a byte in the .npz — the sha256 check
            # must refuse the model, never run it.
            raw = bytearray(model_path.read_bytes())
            raw[100] ^= 0xFF
            model_path.write_bytes(bytes(raw))
            with self.assertRaises(ValueError):
                _model.WakeModel.load(model_path)

    def test_load_refuses_feature_mismatch(self):
        x, y = self._dataset(n_pos=8, n_neg=12)
        model = _model.WakeModel()
        model.train(x, y, epochs=5)
        with tempfile.TemporaryDirectory() as tmp:
            card = _model.WakeModelCard(
                phrase="computer", slug="computer", threshold=0.7,
                n_positive=8, n_negative=12, epochs=5,
                provenance="wav-files")
            model_path, card_path = model.save(Path(tmp), card)
            data = json.loads(card_path.read_text(encoding="utf-8"))
            data["feature_dim"] = 9999
            card_path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(ValueError):
                _model.WakeModel.load(model_path)


class TestStreamingScorer(unittest.TestCase):
    def _trained(self):
        pos = np.stack([_feat.clip_to_vector(_pos_clip(i)) for i in range(16)])
        neg = np.stack([_feat.clip_to_vector(_neg_clip(i)) for i in range(24)])
        x = np.concatenate([pos, neg])
        y = np.concatenate([np.ones(16), np.zeros(24)]).astype(np.float32)
        model = _model.WakeModel()
        model.train(x, y, epochs=60)
        return model

    def test_fires_on_positive_silent_on_negative(self):
        scorer = _model.StreamingWakeScorer(self._trained(), threshold=0.7)
        fired = False
        clip = _pos_clip(50)
        now = 1000.0
        for start in range(0, len(clip), 1600):  # 100 ms chunks
            now += 0.1
            if scorer.feed(clip[start:start + 1600], now=now):
                fired = True
                break
        self.assertTrue(fired, "scorer must fire on the trained phrase")
        self.assertGreaterEqual(scorer.last_score, 0.7)

        scorer2 = _model.StreamingWakeScorer(self._trained(), threshold=0.7)
        now = 2000.0
        for start in range(0, len(clip), 1600):
            now += 0.1
            self.assertFalse(scorer2.feed(_neg_clip(50)[start:start + 1600],
                                          now=now),
                             "scorer must stay silent on a negative")

    def test_refractory_suppresses_second_fire(self):
        scorer = _model.StreamingWakeScorer(self._trained(), threshold=0.7)
        clip = _pos_clip(51)
        first = False
        now = 3000.0
        for start in range(0, len(clip), 1600):
            now += 0.1
            if scorer.feed(clip[start:start + 1600], now=now):
                first = True
                break
        self.assertTrue(first)
        # Same phrase 1 s later: inside the 2.5 s refractory.
        now += 1.0
        fired_again = False
        for start in range(0, len(clip), 1600):
            now += 0.1
            if scorer.feed(clip[start:start + 1600], now=now):
                fired_again = True
        self.assertFalse(fired_again, "refractory must suppress the re-fire")


class TestWakeSynth(unittest.TestCase):
    def test_dataset_bars(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                _synth.build_dataset("computer", [_pos_clip(0)] * 4,
                                     [_neg_clip(i) for i in range(8)],
                                     Path(tmp) / "d", provenance="wav-files")
            with self.assertRaises(ValueError):
                _synth.build_dataset("computer", [_pos_clip(i) for i in range(5)],
                                     [_neg_clip(i) for i in range(7)],
                                     Path(tmp) / "d", provenance="wav-files")

    def test_dataset_from_wavs(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            (src / "positives").mkdir(parents=True)
            (src / "negatives").mkdir(parents=True)
            for i in range(6):
                _feat.write_wav_16k(src / "positives" / f"p{i}.wav", _pos_clip(i))
            for i in range(10):
                _feat.write_wav_16k(src / "negatives" / f"n{i}.wav", _neg_clip(i))
            manifest = _synth.build_dataset_from_wavs(
                "computer", src, Path(tmp) / "dataset")
            self.assertEqual(manifest["n_positive"], 6)
            self.assertEqual(manifest["n_negative"], 10)
            self.assertEqual(manifest["provenance"], "wav-files")
            pos, neg, back = _synth.load_dataset(Path(tmp) / "dataset")
            self.assertEqual(len(pos), 6)
            self.assertEqual(len(neg), 10)

    def test_kokoro_leg_refuses_honestly_when_unprovisioned(self):
        available, _reason = _synth.engine_available()
        if available:
            self.skipTest("kokoro voice is provisioned here")
        pcm, err = _synth.synthesize_text("computer", voice="af_heart")
        self.assertEqual(pcm.size, 0)
        self.assertIsNotNone(err)
        self.assertIn("kokoro", err.lower())


class TestWakeTrainingTools(unittest.TestCase):
    def _wav_dir(self, tmp):
        src = Path(tmp) / "src"
        (src / "positives").mkdir(parents=True)
        (src / "negatives").mkdir(parents=True)
        for i in range(8):
            _feat.write_wav_16k(src / "positives" / f"p{i}.wav", _pos_clip(i))
        for i in range(12):
            _feat.write_wav_16k(src / "negatives" / f"n{i}.wav", _neg_clip(i))
        return src

    def _patched_store(self, tmp):
        """Redirect the model store at BOTH namespaces that hold
        the models_dir binding (wake_model + wake_training)."""
        import unittest.mock as mock

        store = Path(tmp) / "wake"
        store.mkdir(parents=True, exist_ok=True)
        patches = [
            mock.patch.object(_model, "models_dir",
                              lambda: store),
            mock.patch.object(_training, "models_dir",
                              lambda: store),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return store

    def test_train_refuses_bad_phrases(self):
        self.assertIn("2-4 words",
                      _training.train_custom_wake_word(phrase="computer"))
        self.assertIn("duplicates",
                      _training.train_custom_wake_word(phrase="via oak"))
        self.assertIn("cannot be",
                      _training.train_custom_wake_word(phrase=""))

    def test_train_via_wav_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._patched_store(tmp)
            src = self._wav_dir(tmp)
            out = _training.train_custom_wake_word(
                phrase="hey computer", wav_dir=str(src), epochs=30)
            self.assertIn("Trained a custom wake-word model", out)
            self.assertIn("UNEVALUATED", out)
            self.assertIn("Wake itself is still OFF", out)
            self.assertTrue((store / "hey_computer.npz").is_file())
            self.assertTrue((store / "hey_computer.card.json").is_file())
            card = json.loads(
                (store / "hey_computer.card.json").read_text(encoding="utf-8"))
            self.assertFalse(card["evaluated_section10"])
            self.assertEqual(card["provenance"], "wav-files")

    def test_train_refuses_when_kokoro_unavailable(self):
        available, _reason = _synth.engine_available()
        if available:
            self.skipTest("kokoro voice is provisioned here")
        with tempfile.TemporaryDirectory() as tmp:
            self._patched_store(tmp)
            out = _training.train_custom_wake_word(
                phrase="hey computer", epochs=5)
            self.assertIn("Could not synthesise", out)
            self.assertIn("wav_dir", out)

    def test_enable_consent_gate(self):
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            store = self._patched_store(tmp)
            src = self._wav_dir(tmp)
            _training.train_custom_wake_word(
                phrase="hey computer", wav_dir=str(src), epochs=10)
            # Mic refused: nothing is enabled.
            with mock.patch.object(
                    _training.capture_guard, "ensure_capture_allowed",
                    return_value=(False, "mic is off in Settings")):
                out = _training.enable_custom_wake_model("hey_computer")
            self.assertIn("Cannot enable", out)
            self.assertIsNone(_model.get_active(store))
            # Mic granted: the pointer is set (re-read), wake stays off.
            with mock.patch.object(
                    _training.capture_guard, "ensure_capture_allowed",
                    return_value=(True, "granted")):
                out = _training.enable_custom_wake_model("hey_computer")
            self.assertIn("now the active model", out)
            self.assertIn("Wake itself is still OFF", out)
            self.assertEqual(_model.get_active(store)["slug"], "hey_computer")
            detector = _wake_word.WakeWordDetector()
            self.assertFalse(detector.enabled,
                             "enabling a model must not enable wake")

    def test_list_and_status_honest_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._patched_store(tmp)
            self.assertIn("No custom wake-word models",
                          _training.list_custom_wake_models())
            self.assertIn("No custom wake-word model is active",
                          _training.custom_wake_model_status())

    def test_status_never_prompts(self):
        """Read-only means read-only: the status report must not
        call the asking guard (ensure_capture_allowed prompts under
        ask-once/always-ask) — consent is reported from the pure
        reads only."""
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            self._patched_store(tmp)
            with mock.patch.object(
                    _training.capture_guard, "ensure_capture_allowed",
                    side_effect=AssertionError("status asked the user")):
                out = _training.custom_wake_model_status()
            self.assertIn("No custom wake-word model is active", out)
            self.assertIn("Microphone consent:", out)

    def test_detector_arms_trained_model(self):
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as tmp:
            store = self._patched_store(tmp)
            src = self._wav_dir(tmp)
            _training.train_custom_wake_word(
                phrase="hey computer", wav_dir=str(src), epochs=10)
            detector = _wake_word.WakeWordDetector()
            self.assertIsNone(detector._custom_scorer)
            ok, _detail = detector.set_custom_model("hey_computer")
            self.assertTrue(ok)
            self.assertIsNotNone(detector._custom_scorer)
            self.assertEqual(detector._custom_model_slug, "hey_computer")
            self.assertFalse(detector.enabled, "wake stays OFF when armed")
            ok, _detail = detector.set_custom_model("no_such_model")
            self.assertFalse(ok)
            # Disarm.
            ok, _detail = detector.set_custom_model(None)
            self.assertTrue(ok)
            self.assertIsNone(detector._custom_scorer)

    def test_registry_declares_four_tools(self):
        from core.funnel.tools_registry import tools
        from core.funnel import tools_gate as _gate

        names = {d["name"] for d in tools.get_declarations()}
        for name in ("train_custom_wake_word", "enable_custom_wake_model",
                     "list_custom_wake_models", "custom_wake_model_status"):
            self.assertIn(name, names)
        self.assertEqual(
            _gate._FREE_BY_DESIGN.get("train_custom_wake_word"), "wake")
        self.assertEqual(
            _gate._FREE_BY_DESIGN.get("enable_custom_wake_model"), "wake")
        self.assertIn("list_custom_wake_models", _gate._READ_ONLY_TOOLS)
        self.assertIn("custom_wake_model_status", _gate._READ_ONLY_TOOLS)


class TestNoForbiddenRuntime(unittest.TestCase):
    def test_no_tensorflow_torch_or_onnx_imports(self):
        tree = Path(__file__).resolve().parent.parent
        forbidden = ("tensorflow", "torch", "onnxruntime", "onnx")
        for name in ("wake_features", "wake_model", "wake_synth",
                     "wake_training"):
            src = (tree / "core" / "voice" / f"{name}.py").read_text(
                encoding="utf-8")
            parsed = ast.parse(src)
            imported = set()
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            for bad in forbidden:
                self.assertNotIn(bad, imported,
                                 f"{name}.py imports {bad}")
        for bad in forbidden:
            self.assertNotIn(bad, sys.modules,
                             f"{bad} is imported in this process")


if __name__ == "__main__":
    unittest.main()
