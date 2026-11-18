"""Local vision backend (B5) — adapter, facades, licence re-read,
and the two consumers. Heavy runtimes (llama_cpp, transformers,
onnxruntime) are faked; no real weights, no real inference."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from core import vision_backend as vb  # noqa: E402
from core import vision_models as vm  # noqa: E402
from core.pilot_licences_senses import verdict  # noqa: E402

_TREEROOT = Path(__file__).resolve().parent.parent


class _FakeLlama:
    """Stand-in for llama_cpp.Llama with a chat handler."""

    def __init__(self):
        self.calls = 0

    def create_chat_completion(self, messages, max_tokens,
                               temperature):
        self.calls += 1
        # The image_url data URI + text prompt arrive here.
        content = messages[0]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith(
            "data:image/jpeg;base64,")
        assert content[1]["type"] == "text"
        return {"choices": [{"message": {
            "content": "a red circle on a white page"}}]}


class _FakeGraniteRuntime:
    """Stand-in for the transformers processor + 3 ONNX sessions."""

    def __init__(self):
        self.embed_calls = 0
        self.vision_calls = 0
        self.decoder_calls = 0
        text_config = SimpleNamespace(
            num_key_value_heads=2, head_dim=4, num_hidden_layers=2,
            eos_token_id=999)
        self.config = SimpleNamespace(
            text_config=text_config, image_token_id=1234)
        self.processor = _FakeProcessor()
        self.sessions = {
            "vision": _FakeSession("vision", self),
            "embed": _FakeSession("embed", self),
            "decoder": _FakeSession("decoder", self),
        }

    def as_runtime(self):
        return {"config": self.config,
                "processor": self.processor,
                "sessions": self.sessions,
                "dir": Path("/nonexistent")}


class _FakeProcessor:
    def apply_chat_template(self, messages, add_generation_prompt):
        return "<prompt>Convert this page to docling."

    def __call__(self, text, images, return_tensors):
        return {
            "input_ids": np.array([[1, 1234, 5]]),
            "attention_mask": np.ones((1, 3), dtype=np.int64),
            "pixel_values": np.zeros((1, 3, 8, 8), dtype=np.float32),
            "pixel_attention_mask": np.ones(
                (1, 1, 8, 8), dtype=bool),
        }

    def batch_decode(self, tokens, skip_special_tokens=False):
        return ["<doctag><text>hello page</text></doctag>"]


class _FakeSession:
    def __init__(self, name, runtime):
        self.name = name
        self.runtime = runtime

    def run(self, _output_names, feeds):
        if self.name == "embed":
            self.runtime.embed_calls += 1
            return [np.zeros((1, 3, 4), dtype=np.float32)]
        if self.name == "vision":
            self.runtime.vision_calls += 1
            return [np.zeros((1, 1, 4), dtype=np.float32)]
        self.runtime.decoder_calls += 1
        logits = np.zeros((1, 3, 1000), dtype=np.float32)
        logits[0, -1, 999] = 10.0  # eos on the first step
        present = [np.zeros((1, 2, 1, 4), dtype=np.float32)
                   for _ in range(4)]
        return [logits, *present]


class VisionBackendTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.models_dir = Path(self._tmp.name)
        self._old_env = os.environ.get("VYUK_VISION_MODELS_DIR")
        os.environ["VYUK_VISION_MODELS_DIR"] = str(self.models_dir)
        vb._llama_cache.clear()
        vb._llama_failed.clear()
        vb._granite_cache = None
        vb._granite_failed = False

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("VYUK_VISION_MODELS_DIR", None)
        else:
            os.environ["VYUK_VISION_MODELS_DIR"] = self._old_env
        vb._llama_cache.clear()
        vb._llama_failed.clear()
        vb._granite_cache = None
        vb._granite_failed = False

    # -- helpers ------------------------------------------------------
    def _make_qwen_pair(self):
        # Sparse files: the logical size clears the floors without
        # costing disk.
        model = self.models_dir / vb.QWEN3_VL_MODEL_FILE
        mmproj = self.models_dir / vb.QWEN3_VL_MMPROJ_FILE
        with open(model, "wb") as f:
            f.truncate(vb.MIN_QWEN3_MODEL_BYTES + 1)
        with open(mmproj, "wb") as f:
            f.truncate(vb.MIN_QWEN3_MMPROJ_BYTES + 1)
        return model, mmproj

    def _make_granite_snapshot(self):
        snap = self.models_dir / vb.GRANITE_DIR_NAME
        (snap / "onnx").mkdir(parents=True)
        for name in vb.GRANITE_ONNX_FILES:
            (snap / name).touch()
        for name in vb.GRANITE_META_FILES:
            (snap / name).write_text("{}", encoding="utf-8")
        return snap

    def _png_bytes(self, size=(20, 20)):
        from PIL import Image
        import io

        buf = io.BytesIO()
        Image.new("RGB", size, "white").save(buf, format="PNG")
        return buf.getvalue()

    def _fake_llama(self):
        fake = _FakeLlama()
        calls = {"n": 0}

        def _load():
            calls["n"] += 1
            return fake

        return fake, calls, _load

    # -- card re-read -------------------------------------------------
    def test_card_reread_recorded_qwen(self):
        entry = verdict("qwen3-vl")
        self.assertEqual(entry["licence"], "Apache-2.0")
        self.assertEqual(entry["state"], "CLEAR-SEAT")
        self.assertIn("RE-READ 2026-11-06", entry["evidence"])
        self.assertIn("Commercial use allowed", entry["evidence"])
        self.assertEqual(entry["ships"],
                         "backend-wired, weights-not-bundled")

    def test_card_reread_recorded_granite(self):
        entry = verdict("granite-docling-258m")
        self.assertEqual(entry["licence"], "Apache-2.0")
        self.assertEqual(entry["state"], "CLEAR-SEAT")
        self.assertIn("RE-READ 2026-11-06", entry["evidence"])
        self.assertIn("freely available for both research and "
                      "commercial use", entry["evidence"])
        self.assertEqual(entry["ships"],
                         "backend-wired, weights-not-bundled")

    # -- Qwen describe ------------------------------------------------
    def test_describe_returns_model_text_with_timing(self):
        self._make_qwen_pair()
        fake, calls, load = self._fake_llama()
        with mock.patch.object(vb, "_load_qwen3_llama", load), \
                mock.patch.object(vb, "_qwen3_handler_class",
                                  return_value=object):
            result = vb.describe(b"\xff\xd8fake-jpeg", "What is this?")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "a red circle on a white page")
        self.assertEqual(result["engine"], "qwen3-vl")
        self.assertEqual(result["model"], "qwen3-vl-2b")
        self.assertIn("elapsed_s", result)
        self.assertIn("inference_s", result)
        self.assertGreaterEqual(result["elapsed_s"], 0)
        self.assertGreaterEqual(result["inference_s"], 0)

    def test_describe_reuses_loaded_model(self):
        # The REAL loader, with fake llama_cpp modules injected:
        # construction must happen once across two describes.
        self._make_qwen_pair()
        import types

        constructed = {"n": 0}
        fake = _FakeLlama()

        class _FakeLlamaCls:
            def __init__(self, *args, **kwargs):
                constructed["n"] += 1

            def create_chat_completion(self, **kwargs):
                return fake.create_chat_completion(**kwargs)

        class _FakeHandler:
            def __init__(self, *args, **kwargs):
                pass

        llama_mod = types.ModuleType("llama_cpp")
        llama_mod.Llama = _FakeLlamaCls
        fmt_mod = types.ModuleType("llama_cpp.llama_chat_format")
        fmt_mod.Qwen3VLChatHandler = _FakeHandler
        with mock.patch.dict(sys.modules, {
                "llama_cpp": llama_mod,
                "llama_cpp.llama_chat_format": fmt_mod}):
            vb.describe(b"img1")
            vb.describe(b"img2")
        self.assertEqual(constructed["n"], 1)
        self.assertEqual(fake.calls, 2)

    def test_describe_weights_missing_is_honest(self):
        result = vb.describe(b"\xff\xd8fake-jpeg")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "weights-missing")
        self.assertIn("not on disk", result["detail"])
        self.assertNotIn("text", result)

    def test_describe_handler_unavailable_is_honest(self):
        self._make_qwen_pair()
        with mock.patch.object(vb, "_qwen3_handler_class",
                               return_value=None):
            result = vb.describe(b"\xff\xd8fake-jpeg")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "handler-unavailable")

    def test_describe_load_failure_names_cause(self):
        self._make_qwen_pair()

        def _boom():
            raise RuntimeError("CUDA out of memory (fake)")

        with mock.patch.object(vb, "_load_qwen3_llama", _boom), \
                mock.patch.object(vb, "_qwen3_handler_class",
                                  return_value=object):
            result = vb.describe(b"\xff\xd8fake-jpeg")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "load-failed")
        self.assertIn("CUDA out of memory", result["detail"])

    def test_describe_unknown_model_and_4b(self):
        self.assertEqual(
            vb.describe(b"x", model="qwen3-vl-9b")["reason"],
            "unknown-model")
        self.assertEqual(
            vb.describe(b"x", model="qwen3-vl-4b")["reason"],
            "model-not-wired")

    def test_describe_empty_image(self):
        self.assertEqual(vb.describe(b"")["reason"], "empty-image")

    # -- Granite read -------------------------------------------------
    def test_read_page_returns_doctags_with_timing(self):
        self._make_granite_snapshot()
        runtime = _FakeGraniteRuntime()
        with mock.patch.object(vb, "_load_granite",
                               return_value=runtime.as_runtime()):
            result = vb.read_page(self._png_bytes())
        self.assertTrue(result["ok"])
        self.assertIn("hello page", result["text"])
        self.assertEqual(result["text"], result["doctags"])
        self.assertEqual(result["mode"], "full-page")
        self.assertIn("elapsed_s", result)
        self.assertIn("inference_s", result)
        self.assertEqual(runtime.vision_calls, 1)
        self.assertEqual(runtime.decoder_calls, 1)

    def test_read_page_region_mode_crops(self):
        self._make_granite_snapshot()
        runtime = _FakeGraniteRuntime()
        with mock.patch.object(vb, "_load_granite",
                               return_value=runtime.as_runtime()):
            # A real 20x20 PNG so PIL can crop it.
            from PIL import Image
            import io

            buf = io.BytesIO()
            Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
            result = vb.read_page(buf.getvalue(), bbox=(2, 2, 5, 5))
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "region")
        self.assertEqual(result["bbox"], [2, 2, 5, 5])

    def test_read_page_weights_missing_is_honest(self):
        result = vb.read_page(b"\x89PNGfake")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "weights-missing")
        self.assertNotIn("text", result)

    def test_read_page_load_failure_names_cause(self):
        self._make_granite_snapshot()

        def _boom():
            raise RuntimeError("transformers is not installed in "
                               "this build, so the Granite-Docling "
                               "processor cannot run")

        with mock.patch.object(vb, "_load_granite", _boom):
            result = vb.read_page(self._png_bytes())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "transformers-absent")

    def test_read_page_bad_image(self):
        self._make_granite_snapshot()
        result = vb.read_page(b"not-an-image-at-all")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "bad-image")

    def test_granite_real_loader_reports_missing_runtime(self):
        # In this lab transformers is absent: the real loader must
        # say so honestly instead of crashing.
        self._make_granite_snapshot()
        try:
            import transformers  # noqa: F401
            self.skipTest("transformers is installed here")
        except ImportError:
            pass
        with self.assertRaises(RuntimeError) as ctx:
            vb._load_granite()
        self.assertIn("transformers is not installed", str(ctx.exception))

    # -- status + facades ---------------------------------------------
    def test_vision_status_shape(self):
        status = vb.vision_status()
        self.assertIn("qwen3-vl-2b", status)
        self.assertIn("granite-docling-258m", status)
        self.assertFalse(status["qwen3-vl-2b"]["ready"])
        self.assertFalse(status["granite-docling-258m"]["ready"])

    def test_facade_qwen_routes_to_backend(self):
        self._make_qwen_pair()
        fake, _, load = self._fake_llama()
        with mock.patch.object(vb, "_load_qwen3_llama", load), \
                mock.patch.object(vb, "_qwen3_handler_class",
                                  return_value=object):
            result = vm.qwen_describe(b"\xff\xd8fake", "Describe it.")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "a red circle on a white page")

    def test_facade_qwen_weights_missing(self):
        result = vm.qwen_describe(b"\xff\xd8fake", "Describe it.")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "weights-missing")

    def test_facade_granite_routes_to_backend(self):
        self._make_granite_snapshot()
        runtime = _FakeGraniteRuntime()
        with mock.patch.object(vb, "_load_granite",
                               return_value=runtime.as_runtime()):
            result = vm.granite_read_page(self._png_bytes())
        self.assertTrue(result["ok"])
        self.assertIn("hello page", result["text"])

    def test_facade_granite_licence_blocked(self):
        with mock.patch.object(vm, "shippable", return_value=False):
            result = vm.granite_read_page(b"\x89PNGfake")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "licence-blocked")

    def test_no_weights_bundled_in_tree(self):
        # The batch never bundles weights: no .gguf/.onnx under
        # the tree (outside the data/ runtime dir, which is
        # excluded from the product diff anyway).
        bundled = [p for p in _TREEROOT.rglob("*.gguf")
                   if "data" not in p.parts]
        bundled += [p for p in _TREEROOT.rglob("*.onnx")
                    if "data" not in p.parts]
        self.assertEqual(bundled, [])

    # -- consumers ------------------------------------------------------
    def test_frame_describe_uses_adapter_when_ready(self):
        from core.voice import local_client_vlm as vlm_mod

        with mock.patch.object(
                vb, "describe",
                return_value={"ok": True,
                              "text": "a login window"}) as patched:
            with mock.patch.object(vlm_mod.local_assets, "vlm_ready",
                                   return_value=False):
                self.assertEqual(
                    vlm_mod.describe_frame_vlm(b"frame-bytes"),
                    "a login window")
        patched.assert_called_once()

    def test_frame_describe_falls_back_when_adapter_cannot(self):
        from core.voice import local_client_vlm as vlm_mod

        with mock.patch.object(
                vb, "describe",
                return_value={"ok": False,
                              "reason": "weights-missing",
                              "detail": "not on disk"}):
            with mock.patch.object(vlm_mod.local_assets, "vlm_ready",
                                   return_value=False):
                self.assertIsNone(
                    vlm_mod.describe_frame_vlm(b"frame-bytes"))

    def test_screenshot_note_uses_granite_when_ready(self):
        from core.skills_files5_watch_blur_notes import (
            screenshot_to_text_note)

        shot = self.models_dir / "shot.png"
        from PIL import Image

        Image.new("RGB", (10, 10), "white").save(shot)
        with mock.patch.object(
                vb, "read_page",
                return_value={"ok": True,
                              "text": "<doctag><text>invoice "
                                      "total 42</text></doctag>"}):
            message = screenshot_to_text_note(str(shot))
        self.assertIn("Granite-Docling structured read", message)
        notes = list(self.models_dir.glob("shot_text_*.txt"))
        self.assertEqual(len(notes), 1)
        self.assertIn("invoice total 42",
                      notes[0].read_text(encoding="utf-8"))
        self.assertIn("Granite-Docling structured read",
                      notes[0].read_text(encoding="utf-8"))

    def test_screenshot_note_falls_back_to_ocr(self):
        from core.skills_files5_watch_blur_notes import (
            screenshot_to_text_note)
        import core.skills_files5_watch_blur_notes as notes_mod

        shot = self.models_dir / "shot2.png"
        from PIL import Image

        Image.new("RGB", (10, 10), "white").save(shot)
        with mock.patch.object(
                vb, "read_page",
                return_value={"ok": False,
                              "reason": "weights-missing",
                              "detail": "not on disk"}), \
                mock.patch.object(
                    notes_mod, "_ocr_image_text",
                    return_value=("plain ocr words", None)):
            message = screenshot_to_text_note(str(shot))
        self.assertIn("Tesseract OCR", message)
        self.assertIn("plain ocr words", message)


if __name__ == "__main__":
    unittest.main()
