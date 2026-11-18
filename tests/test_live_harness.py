"""Live-session harness currency (field-check fix #34, V3).

The harness (custom.gemini/bin/live_session_test.py) went stale three
ways: it inserted the dead LAB/.pkgroot into sys.path, it imported the
moved `voice_assistant.core.live_client`, and CONNECTOR.md named the
numpy-less .venv-gx3. These tests pin the fixed shape WITHOUT a live
model call — the live turn itself is proven by the repro run
(fieldfix_v3harness_work/run_repro.sh), not by a unit test.
"""
import ast
import importlib.util
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import types as _types  # noqa: E402

if "voice_assistant" not in sys.modules:
    _pkg = _types.ModuleType("voice_assistant")
    _pkg.__path__ = [str(ROOT)]
    sys.modules["voice_assistant"] = _pkg
if "voice_assistant.core" not in sys.modules:
    _core = _types.ModuleType("voice_assistant.core")
    _core.__path__ = [str(ROOT / "core")]
    sys.modules["voice_assistant.core"] = _core

from voice_assistant.core.voice import live_client  # noqa: E402

HOME = pathlib.Path.home()
HARNESS = (HOME / "workspace" / "custom-connectors" / "custom.gemini"
           / "bin" / "live_session_test.py")
CONNECTOR = (HOME / "workspace" / "custom-connectors" / "custom.gemini"
             / "CONNECTOR.md")
PCM = HOME / "workspace" / "code-lab" / ".livetest" / "speech_16k.pcm"
LIVE_TREE = (HOME / "workspace" / "code-lab" / "imported"
             / "voice-ai-assistant" / "voice-ai-assistant")


def _documented_venv() -> str | None:
    text = CONNECTOR.read_text(encoding="utf-8")
    m = re.search(r"((?:~|/home/hatch)/workspace/code-lab/\.venv-[^/\s]+"
                  r"/bin/python)", text)
    return str(pathlib.Path(m.group(1)).expanduser()) if m else None


class TestLiveHarness(unittest.TestCase):
    def test_harness_does_not_reference_dead_pkgroot(self):
        src = HARNESS.read_text(encoding="utf-8")
        self.assertNotIn('".pkgroot"', src)
        self.assertNotIn("'.pkgroot'", src)
        self.assertNotIn('LAB / ".pkgroot"', src)

    def test_harness_imports_moved_live_client(self):
        src = HARNESS.read_text(encoding="utf-8")
        self.assertIn("voice_assistant.core.voice.live_client", src)
        self.assertNotIn(
            "from voice_assistant.core.live_client import", src)

    def test_harness_imports_resolve_against_current_tree(self):
        from voice_assistant.core.config import config
        from voice_assistant.core.voice.live_client import (
            GeminiLiveSession)
        self.assertTrue(hasattr(config, "api_key"))
        config.api_key = "harness-shape-probe"
        self.assertEqual(config.api_key, "harness-shape-probe")
        config.api_key = ""
        self.assertEqual(
            GeminiLiveSession.__module__,
            "voice_assistant.core.voice.live_client")
        self.assertTrue(
            live_client.__file__.endswith(
                "core/voice/live_client.py"))

    def test_harness_tree_default_is_live_tree(self):
        src = HARNESS.read_text(encoding="utf-8")
        self.assertIn("imported/voice-ai-assistant", src)
        self.assertTrue((LIVE_TREE / "core" / "voice"
                         / "live_client.py").is_file())

    def test_connector_names_venv_qt_not_gx3(self):
        text = CONNECTOR.read_text(encoding="utf-8")
        self.assertNotIn(".venv-gx3", text)
        self.assertIn(".venv-qt/bin/python", text)
        venv = _documented_venv()
        self.assertIsNotNone(venv)
        self.assertTrue(pathlib.Path(venv).is_file())

    def test_documented_interpreter_has_harness_deps(self):
        venv = _documented_venv()
        self.assertIsNotNone(venv)
        r = subprocess.run(
            [venv, "-c",
             "import numpy, google.genai, websockets; "
             "print(numpy.__version__, google.genai.__version__, "
             "websockets.__version__)"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn(".", r.stdout.strip())

    def test_speech_clip_exists_and_is_pcm(self):
        self.assertTrue(PCM.is_file())
        size = PCM.stat().st_size
        self.assertGreater(size, 0)
        self.assertEqual(size % 2, 0, "int16 PCM must be even-sized")


if __name__ == "__main__":
    unittest.main()
