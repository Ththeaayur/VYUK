"""Sweep Build 5 — sherpa code path, KWS/TTS only (code only, no model).

What is tested: the pinned Apache-2.0 licence file (actual wheel text, not
a README quotation), the Qt-free code-path interface, fail-closed model
loading, and the absence of any model file in the tree.

NOT claimed here: working KWS, working TTS, a loaded model, or any field
behaviour — those need a per-model card + licence read, a shipped model,
and field proof on his machine, none of which this build provides.
"""

import hashlib
import pathlib
import unittest

from core import sherpa_code_path as sherpa

_ROOT = pathlib.Path(__file__).resolve().parent.parent

_EXPECTED_SHA = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"


class TestSherpaLicence(unittest.TestCase):
    def test_licence_file_present_and_apache(self):
        licence = _ROOT / "gui" / "assets" / "sherpa" / "LICENSE.txt"
        self.assertTrue(licence.is_file(), "pinned sherpa LICENSE.txt missing")
        text = licence.read_text(encoding="utf-8", errors="strict")
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0, January 2004", text)
        self.assertIn("TERMS AND CONDITIONS FOR USE, REPRODUCTION, "
                      "AND DISTRIBUTION", text)
        self.assertEqual(licence.stat().st_size, 11358)
        self.assertEqual(hashlib.sha256(licence.read_bytes()).hexdigest(),
                         _EXPECTED_SHA)

    def test_licence_read_records_pin_and_sha(self):
        read = _ROOT / "gui" / "assets" / "SHERPA_LICENCE_READ.md"
        self.assertTrue(read.is_file(), "SHERPA_LICENCE_READ.md missing")
        text = read.read_text(encoding="utf-8")
        self.assertIn("2.0.0", text)
        self.assertIn(_EXPECTED_SHA, text)
        self.assertIn("Apache-2.0", text)
        self.assertIn("model licence read owed", text.lower())
        # The read must not pretend a model is cleared.
        self.assertIn("NOT COVERED", text)

    def test_no_sherpa_runtime_dependency_added(self):
        req = _ROOT / "requirements.txt"
        if req.is_file():
            self.assertNotIn("sherpa", req.read_text(encoding="utf-8").lower())


class TestSherpaCodePath(unittest.TestCase):
    def test_status_is_code_path_only(self):
        status = sherpa.code_path_status()
        self.assertEqual(status["package"], "sherpa_onnx")
        self.assertEqual(status["version"], "2.0.0")
        self.assertEqual(status["code_licence"], "Apache-2.0")
        self.assertEqual(status["scope"], ["kws", "tts"])
        self.assertFalse(status["runtime_installed"])
        self.assertFalse(status["runtime_imported"])
        self.assertFalse(status["model_loaded"])
        self.assertFalse(status["model_shipped"])
        self.assertEqual(status["wake_status"], "OFF")

    def test_module_does_not_import_sherpa_runtime(self):
        source = (_ROOT / "core" / "sherpa_code_path.py").read_text(encoding="utf-8")
        self.assertNotIn("import sherpa_onnx", source)
        self.assertNotIn("from sherpa_onnx", source)

    def test_gate_evidence_shape(self):
        record = sherpa.licence_read_record()
        self.assertEqual(record["item"], "sherpa_kws_tts")
        self.assertTrue(record["licence_file"])
        self.assertTrue(record["licence_sha256"])
        self.assertEqual(record["model_licence"], "owed_per_model")

    def test_kws_tts_configs_are_templates_only(self):
        for cfg in (sherpa.kws_config(), sherpa.tts_config()):
            self.assertIsNone(cfg["model"])
            self.assertFalse(cfg["model_file_present"])
            self.assertEqual(cfg["status"], "not_loaded")

    def test_available_models_empty(self):
        self.assertEqual(sherpa.available_models(), [])


class TestSherpaFailClosed(unittest.TestCase):
    def test_card_without_licence_read_refuses_to_load(self):
        card = sherpa.model_card("example-kws", "kws")
        self.assertEqual(card["status"], "not_shipped")
        self.assertIsNone(card["licence_read"])
        result = sherpa.load_model(card)
        self.assertFalse(result["ok"])
        self.assertIn("licence read owed", result["reason"])

    def test_card_with_licence_read_still_refuses_without_model(self):
        card = {"name": "example-tts", "kind": "tts",
                "licence_read": "some future per-model read record"}
        result = sherpa.load_model(card)
        self.assertFalse(result["ok"])
        self.assertIn("no model file is present", result["reason"])

    def test_out_of_scope_kind_refused(self):
        card = sherpa.model_card("example-asr", "asr")
        self.assertFalse(card["ok"])
        self.assertEqual(card["status"], "refused_out_of_scope")
        self.assertFalse(sherpa.load_model(card)["ok"])

    def test_no_model_file_in_tree(self):
        onnx = [p for p in _ROOT.rglob("*.onnx")
                if "__pycache__" not in p.parts]
        self.assertEqual(onnx, [], f"model files present: {onnx}")


if __name__ == "__main__":
    unittest.main()
