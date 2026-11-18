"""B-R1-3r tests: the Kokoro subprocess boundary
(core/desktop/kokoro_isolate.py + kokoro_child.py).

Fake child + synthetic PCM — no real mic, no real audio, no
speakers, no Kokoro assets. The real-child legs run only where
kokoro-onnx is installed (skipped otherwise): in .venv-kokoro
the real child script launches and answers its ping, and a
render request fails honestly on the missing assets.
"""

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from core.desktop import kokoro_isolate as ki
from core.desktop import kokoro_voice as kv

TREE_ROOT = Path(__file__).resolve().parent.parent
CHILD = TREE_ROOT / "core" / "desktop" / "kokoro_child.py"
VOICES = ("am_michael", "bm_george", "hm_omega", "jf_alpha",
          "zm_yunjian", "em_alex", "ff_siwis", "im_nicola", "pm_alex")

# The GPL engine names. A main-process module must never import
# any of them (AST Import/ImportFrom nodes only — a string such
# as doctor.py's find_spec("kokoro_onnx") presence probe is not
# an import and is out of this set by construction).
FORBIDDEN = ("kokoro_onnx", "phonemizer", "espeakng_loader",
             "espeak_ng", "espeak")

FAKE_CHILD = '''\
"""Fake Kokoro child for tests: same JSON-line protocol, synthetic PCM."""
import array
import base64
import json
import sys
import time


def respond(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()


def tone():
    raw = array.array("f", [0.5] * 12000).tobytes()
    return {"ok": True, "protocol": 1, "rate": 24000, "samples": 12000,
            "pcm_b64": base64.b64encode(raw).decode("ascii")}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "tone"
    if mode == "crash":
        sys.exit(1)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            respond({"ok": False, "reason": "not json"})
            continue
        if req.get("command") == "ping":
            respond({"ok": True, "protocol": 1, "command": "ping"})
            continue
        if mode == "slow":
            time.sleep(30)
            respond(tone())
        elif mode == "garbage":
            sys.stdout.write("not json\\n")
            sys.stdout.flush()
            sys.exit(1)
        elif mode == "refuse":
            respond({"ok": False, "protocol": 1,
                     "reason": "voice not in bin"})
        else:
            respond(tone())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_TMPDIR = None
FAKE_PATH = None


def setUpModule():
    global _TMPDIR, FAKE_PATH
    _TMPDIR = tempfile.mkdtemp(prefix="kokoro_isolate_test_")
    FAKE_PATH = os.path.join(_TMPDIR, "fake_kokoro_child.py")
    with open(FAKE_PATH, "w", encoding="utf-8") as f:
        f.write(FAKE_CHILD)


def tearDownModule():
    global _TMPDIR, FAKE_PATH
    if _TMPDIR:
        shutil.rmtree(_TMPDIR, ignore_errors=True)
    _TMPDIR = None
    FAKE_PATH = None


def _importers():
    """{module name: {files importing it}} by AST over the tree."""
    found = {name: set() for name in FORBIDDEN}
    for path in sorted(TREE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — an unparsable file fails loudly
            raise AssertionError(f"{path} does not parse: {e}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in found:
                        found[top].add(str(path.relative_to(TREE_ROOT)))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top in found:
                        found[top].add(str(path.relative_to(TREE_ROOT)))
    return found


def _request(**over):
    req = {"text": "Hello there", "voice": "bm_george", "lang": "en-gb",
           "speed": 1.0, "model_path": "/tmp/nope.onnx",
           "voices_path": "/tmp/nope.bin"}
    req.update(over)
    return req


class ChildBackend:
    """Test adapter: the KokoroSpeaker backend shape over a
    KokoroChild (the fake child ignores the model paths)."""

    def __init__(self, child, voices=VOICES):
        self._child = child
        self._voices = tuple(voices)
        self.calls = []

    @property
    def backend(self):
        return "kokoro-onnx"

    def voices(self):
        return self._voices

    def speak(self, text, voice, speed, lang):
        self.calls.append((text, voice, speed, lang))
        out = self._child.synthesize({
            "text": text, "voice": voice, "speed": speed, "lang": lang,
            "model_path": "/tmp/nope.onnx",
            "voices_path": "/tmp/nope.bin"})
        if not out["ok"]:
            raise RuntimeError(out["reason"])
        return out["samples"], out["rate"]

    def reset(self):
        self._child.close()


class TestNoMainProcessImport(unittest.TestCase):
    def test_no_main_process_module_imports_gpl_engine(self):
        found = _importers()
        # B-R1-3s (2026-11-09): the ONLY kokoro_onnx importer in
        # the tree is the child entry point itself. The session
        # TTS path was the named B-R1-3r residual; it routes
        # through the boundary now, so the residual is GONE. If
        # this test fails with a new importer, the isolation has
        # a live bypass — the fix is to route it through the
        # boundary, not to widen this set.
        self.assertEqual(
            found["kokoro_onnx"],
            {"core/desktop/kokoro_child.py"},
            found)
        # The wrapper, the boundary, and the session path must
        # never import it.
        self.assertNotIn("core/desktop/kokoro_voice.py",
                         found["kokoro_onnx"])
        self.assertNotIn("core/desktop/kokoro_isolate.py",
                         found["kokoro_onnx"])
        self.assertNotIn("core/voice/local_client_voices.py",
                         found["kokoro_onnx"])
        self.assertNotIn("core/voice/local_client_speech.py",
                         found["kokoro_onnx"])
        # Nobody imports the phoneme stack directly at all.
        for name in ("phonemizer", "espeakng_loader", "espeak_ng",
                     "espeak"):
            self.assertEqual(found[name], set(), name)

    def test_child_imports_engine_only_inside_main(self):
        tree = ast.parse(CHILD.read_text(encoding="utf-8"))
        # No module-level import of the engine.
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names]
                modules = [node.module] if isinstance(
                    node, ast.ImportFrom) else []
                for name in names + modules:
                    self.assertFalse(
                        (name or "").split(".")[0] == "kokoro_onnx",
                        f"module-level kokoro_onnx import in {CHILD}")
        # Every kokoro_onnx import anywhere is inside a function.
        inside_function = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Import):
                        inside_function += [
                            a.name for a in sub.names]
                    elif isinstance(sub, ast.ImportFrom):
                        inside_function.append(sub.module or "")
        self.assertIn("kokoro_onnx", inside_function)
        # The child imports NO tree module at all.
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                self.fail(f"relative import in {CHILD}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0],
                                     ("core", "voice_assistant", "gui"),
                                     f"tree import in {CHILD}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0],
                                 ("core", "voice_assistant", "gui"),
                                 f"tree import in {CHILD}")


class TestBoundary(unittest.TestCase):
    def _child(self, mode="tone", **kw):
        child = ki.KokoroChild(python=sys.executable,
                               child_script=FAKE_PATH,
                               extra_args=(mode,), **kw)
        self.addCleanup(child.close)
        return child

    def test_synthesis_through_boundary_returns_pcm(self):
        child = self._child()
        out = child.synthesize(_request())
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["rate"], 24000)
        self.assertEqual(len(out["samples"]), 12000)
        self.assertAlmostEqual(float(np.max(out["samples"])), 0.5)
        self.assertTrue(child.is_alive)  # persistent: still up for call 2
        out2 = child.synthesize(_request(text="Second call"))
        self.assertTrue(out2["ok"], out2)
        self.assertEqual(len(out2["samples"]), 12000)

    def test_child_crash_is_honest_failure(self):
        child = self._child("crash")
        out = child.synthesize(_request())
        self.assertFalse(out["ok"])
        self.assertIn("child", out["reason"])
        self.assertNotIn("samples", out)
        self.assertFalse(child.is_alive)
        # No silent in-process fallback: the main process never
        # imported the engine, even on failure.
        self.assertNotIn("kokoro_onnx", sys.modules)
        self.assertNotIn("phonemizer", sys.modules)

    def test_child_timeout_is_honest_failure_never_a_hang(self):
        child = self._child("slow", timeout_s=0.5)
        start = time.monotonic()
        out = child.synthesize(_request())
        elapsed = time.monotonic() - start
        self.assertFalse(out["ok"])
        self.assertIn("did not answer within", out["reason"])
        self.assertLess(elapsed, 10.0, "a timeout must not hang")
        self.assertFalse(child.is_alive)  # killed on timeout

    def test_child_dies_on_reset(self):
        child = self._child()
        out = child.synthesize(_request())
        self.assertTrue(out["ok"], out)
        self.assertTrue(child.is_alive)
        child.reset()
        self.assertFalse(child.is_alive)
        # A fresh call after reset spawns a new child.
        out = child.synthesize(_request())
        self.assertTrue(out["ok"], out)
        self.assertTrue(child.is_alive)

    def test_child_dies_on_close(self):
        child = self._child()
        self.assertTrue(child.synthesize(_request())["ok"])
        self.assertTrue(child.is_alive)
        child.close()
        self.assertFalse(child.is_alive)
        child.close()  # idempotent

    def test_child_dies_on_stdin_eof(self):
        # The parent-death watch, directly: close the child's
        # stdin and it must exit on its own, promptly, code 0.
        proc = subprocess.Popen(
            [sys.executable, str(CHILD)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        try:
            proc.stdin.write(json.dumps({"command": "ping"}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            self.assertEqual(json.loads(line).get("command"), "ping")
            proc.stdin.close()
            rc = proc.wait(timeout=5)
            self.assertEqual(rc, 0)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_non_text_input_is_refused_without_a_child(self):
        child = self._child()
        for bad in (None, 42, 3.5, ["hello"], {"text": "hello"}):
            out = child.synthesize(_request(text=bad))
            self.assertFalse(out["ok"], bad)
            self.assertIn("text must be a non-empty string", out["reason"])
        self.assertFalse(child.is_alive)  # never spawned to refuse

    def test_bad_speed_and_missing_fields_refused(self):
        child = self._child()
        for bad in (0, -1.0, 4.5, float("nan"), "fast", True):
            out = child.synthesize(_request(speed=bad))
            self.assertFalse(out["ok"], bad)
        out = child.synthesize(_request(voice=""))
        self.assertFalse(out["ok"])
        self.assertFalse(child.is_alive)

    def test_malformed_child_output_is_honest_failure(self):
        child = self._child("garbage")
        out = child.synthesize(_request())
        self.assertFalse(out["ok"])
        self.assertIn("child", out["reason"])
        self.assertFalse(child.is_alive)

    def test_child_render_refusal_is_honest_failure(self):
        child = self._child("refuse")
        out = child.synthesize(_request())
        self.assertFalse(out["ok"])
        self.assertIn("voice", out["reason"])
        self.assertFalse(child.is_alive)  # poisoned session never survives

    def test_bad_timeout_is_refused_at_construction(self):
        for bad in (0, -1, float("nan"), "fast", True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                ki.KokoroChild(timeout_s=bad)


class TestProbe(unittest.TestCase):
    def test_probe_matches_find_spec(self):
        expected = importlib.util.find_spec("kokoro_onnx") is not None
        probe = ki.probe_engine(refresh=True)
        self.assertEqual(probe["available"], expected, probe)
        self.assertEqual(probe["python"], sys.executable)
        # Cached: a second call without refresh agrees.
        self.assertEqual(ki.probe_engine()["available"], expected)

    def test_real_child_ping_when_engine_present(self):
        if importlib.util.find_spec("kokoro_onnx") is None:
            self.skipTest("kokoro-onnx not installed in this interpreter")
        # The REAL child script launches and answers its ping in
        # the interpreter that has kokoro-onnx; a render then
        # fails honestly on the missing assets (no assets on disk
        # in this lab) — never with a silent fallback, never by
        # importing the engine in THIS process.
        child = ki.KokoroChild(python=sys.executable,
                               child_script=str(CHILD))
        self.addCleanup(child.close)
        out = child.synthesize(_request())
        self.assertFalse(out["ok"])
        self.assertIn("not on disk", out["reason"])
        self.assertNotIn("kokoro_onnx", sys.modules)
        self.assertNotIn("phonemizer", sys.modules)


class TestWrapperThroughBoundary(unittest.TestCase):
    def test_wrapper_speaks_through_boundary(self):
        child = ki.KokoroChild(python=sys.executable,
                               child_script=FAKE_PATH,
                               extra_args=("tone",))
        self.addCleanup(child.close)
        det = kv.KokoroSpeaker(backend=ChildBackend(child))
        out = det.speak("Hello there", voice="bm_george", language="en-gb")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["voice"], "bm_george")
        self.assertEqual(out["sample_rate"], 24000)
        self.assertEqual(len(out["audio"]), 12000 * 2)
        self.assertTrue(out["verification"]["ok"])
        # The wrapper's discipline is unchanged through the
        # boundary: a missing voice is refused before any render.
        backend = det._backend
        out = det.speak("Hello", voice="af_heart")
        self.assertFalse(out["ok"])
        self.assertIn("af_heart", out["reason"])
        self.assertIsNone(out["audio"])
        self.assertEqual(len(backend.calls), 1)  # no render attempted

    def test_engine_unavailable_without_kokoro_onnx(self):
        # The wrapper's real backend still degrades honestly
        # where the engine is absent — now via the subprocess
        # probe, never an in-process import.
        if kv.engine_available():
            self.skipTest("kokoro-onnx present in this interpreter")
        out = kv.KokoroSpeaker().speak("Hello", voice="bm_george")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "engine_unavailable")
        self.assertIsNone(out["audio"])
        self.assertNotIn("kokoro_onnx", sys.modules)


if __name__ == "__main__":
    unittest.main()
