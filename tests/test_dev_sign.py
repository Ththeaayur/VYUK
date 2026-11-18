"""Dev-sign repair (capability fix B7).

    dev_sign_manifest.py — the keyless dev-sign driver at the
    code-lab root — crashed with ModuleNotFoundError on the current
    tree: Phase 3 H1 moved integrity.py from core/ to
    core/health/, packaging/gen_install_manifest.py was repaired
    2026-11-05, this script was not. B7 repairs it to the moved
    path (loud refusal when the import fails) and pins the repair
    here.

    What this suite proves:
    - the moved path is the only path (no core/integrity.py left);
    - the tree never references the out-of-tree script (it lives
      at the code-lab root; the tree must not point at it);
    - the repaired script runs end to end on a synthetic tree
      built from the REAL core/health/integrity.py (+ updater.py
      + updater_verify.py), producing a manifest whose bytes are
      the tree's own canonical bytes, signed with an ephemeral
      dev seed: the .sig verifies against the printed dev public
      key and does NOT verify against the pinned release key
      (dev-only by construction, never release-signed);
    - the script's docstring names the moved paths, not the stale
      ones;
    - the real signing flow (packaging/gen_install_manifest.py +
      sign_manifest.py) is intact and its crypto round-trips.
      The live tree's own install_manifest.json/.sig are format-
      checked only: the manifest is stale vs the lab tree by
      design until the v6.3.0 release re-gens it, so no content
      assertion is made about it here.

    No test here touches the release signing key: every signature
    is made with a fresh ephemeral os.urandom(32) seed.
"""

import ast
import base64
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
# CODE_LAB is the code-lab root the script lives under, found by
# walking up from TREE until dev_sign_manifest.py is present —
# so the suite works at any tree depth (the live tree sits three
# levels under code-lab; a shallow backup sits directly under it,
# where parents[2] would resolve to the wrong directory).
def _find_code_lab():
    for candidate in (TREE, *TREE.parents):
        if (candidate / "dev_sign_manifest.py").is_file():
            return candidate
    return TREE.parents[2]


CODE_LAB = _find_code_lab()
SCRIPT = CODE_LAB / "dev_sign_manifest.py"
KIT = CODE_LAB / "packaging"

# The pinned release public key hex, read from the tree's own
# updater_verify.py (public by design — it is pinned in code).
_PINNED_RE = re.compile(
    r'UPDATE_FEED_PUBLIC_KEY_HEX\s*=\s*\\\s*\n\s*"([0-9a-f]{64})"')


def _pinned_public_key() -> bytes:
    text = (TREE / "core" / "health" / "updater_verify.py").read_text(
        encoding="utf-8")
    match = _PINNED_RE.search(text)
    assert match is not None, "pinned key hex not found in updater_verify.py"
    return bytes.fromhex(match.group(1))


def _load_synth_integrity(root: Path):
    """The synthetic tree's REAL core/health/integrity module,
    imported the way the script and gen_install_manifest.py do
    it (fake package registration); sys.modules entries are
    restored afterwards so nothing leaks into other suites.

    The synthetic tree carries only the three health modules, so
    the updater import chain's fallback (`from core.health.updater
    import ...`, the app-root context) resolves through the REAL
    tree root, inserted on sys.path for the import only — that
    code is byte-identical to the copies, so the verifier is the
    real one. Without this the import degrades to
    _ed25519_verify = None (fail-closed) and the caller must
    treat that as a failure, never as a pass."""
    saved = {name: sys.modules.get(name) for name in (
        "voice_assistant", "voice_assistant.core",
        "voice_assistant.core.health")}
    sys.path.insert(0, str(TREE))
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [str(root)]
    sys.modules["voice_assistant"] = pkg
    core_pkg = types.ModuleType("voice_assistant.core")
    core_pkg.__path__ = [str(root / "core")]
    sys.modules["voice_assistant.core"] = core_pkg
    health_pkg = types.ModuleType("voice_assistant.core.health")
    health_pkg.__path__ = [str(root / "core" / "health")]
    sys.modules["voice_assistant.core.health"] = health_pkg
    try:
        return importlib.import_module(
            "voice_assistant.core.health.integrity")
    finally:
        sys.path.remove(str(TREE))
        for name in list(sys.modules):
            if name == "voice_assistant" or name.startswith(
                    "voice_assistant."):
                sys.modules.pop(name, None)
        for name, old in saved.items():
            if old is not None:
                sys.modules[name] = old


class DevSignRepairTests(unittest.TestCase):
    def test_integrity_moved_path_pinned(self):
        # The old flat path is gone; the moved path is the only one,
        # and it exposes the builder + canonical serialiser the
        # script (and the real flow) depend on.
        self.assertFalse((TREE / "core" / "integrity.py").exists(),
                         "stale core/integrity.py still exists")
        integrity_path = TREE / "core" / "health" / "integrity.py"
        self.assertTrue(integrity_path.is_file())
        tree = ast.parse(integrity_path.read_text(encoding="utf-8"))
        defined = {n.name for n in tree.body
                   if isinstance(n, ast.FunctionDef)}
        for name in ("build_manifest", "manifest_bytes"):
            self.assertIn(name, defined,
                          f"core/health/integrity.py lacks {name}")

    def test_no_tree_references_to_dev_sign(self):
        # The script lives at the code-lab root, outside the tree;
        # nothing inside the tree may point at it. (This test file
        # itself names it, so it is excluded by name.)
        hits = []
        for path in sorted(TREE.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or "data" in path.parts:
                continue
            if path.name == "test_dev_sign.py":
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (UnicodeDecodeError, OSError):
                continue
            if "dev_sign_manifest" in text:
                hits.append(str(path.relative_to(TREE)))
        self.assertEqual(hits, [], f"tree references dev_sign_manifest: {hits}")

    def test_script_docstring_names_moved_paths(self):
        self.assertTrue(SCRIPT.is_file(), f"script missing: {SCRIPT}")
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("core/health/integrity.py", text)
        self.assertIn("core/health/updater_verify.py", text)
        self.assertNotIn("core/integrity.py", text,
                         "docstring still names the stale flat path")
        self.assertNotIn("core/updater.py", text,
                         "docstring still names the stale updater path")
        self.assertIn("voice_assistant.core.health.integrity", text)

    def test_repaired_script_runs_end_to_end(self):
        # A minimal synthetic tree built from the REAL
        # core/health/*.py, so the script is exercised against the
        # real builder, verifier and pinned key — not a stub.
        tmp = Path(tempfile.mkdtemp(prefix="devsign_test_"))
        try:
            health = tmp / "core" / "health"
            health.mkdir(parents=True)
            for name in ("integrity.py", "updater.py", "updater_verify.py"):
                shutil.copyfile(
                    TREE / "core" / "health" / name, health / name)
            (tmp / "app.py").write_text("print('hi')\n", encoding="utf-8")
            sub = tmp / "sub"
            sub.mkdir()
            (sub / "mod.py").write_text("X = 1\n", encoding="utf-8")
            (tmp / "data").mkdir()
            (tmp / "data" / "runtime.json").write_text("{}\n",
                                                       encoding="utf-8")

            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(tmp)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 0,
                             f"script failed: {proc.stderr}")
            self.assertIn("DEV-ONLY", proc.stdout)

            integrity = _load_synth_integrity(tmp)
            self.assertIsNotNone(integrity._ed25519_verify,
                                 "real verifier did not import")
            manifest = integrity.build_manifest(tmp)
            canonical = integrity.manifest_bytes(manifest)
            manifest_path = tmp / "install_manifest.json"
            sig_path = tmp / "install_manifest.json.sig"
            self.assertTrue(manifest_path.is_file())
            self.assertTrue(sig_path.is_file())
            # The written bytes ARE the tree's own canonical bytes.
            self.assertEqual(manifest_path.read_bytes(), canonical)
            # The manifest never covers data/ or itself.
            self.assertNotIn("install_manifest.json", manifest["files"])
            self.assertNotIn("install_manifest.json.sig",
                             manifest["files"])
            self.assertFalse(any(p.startswith("data/")
                                 for p in manifest["files"]))

            dev_pub_hex = re.search(
                r"dev public key hex: ([0-9a-f]{64})", proc.stdout)
            self.assertIsNotNone(dev_pub_hex, "no dev key printed")
            dev_pub = bytes.fromhex(dev_pub_hex.group(1))
            sig = base64.b64decode(sig_path.read_text(
                encoding="utf-8").strip())
            self.assertEqual(len(sig), 64, "signature is not 64 bytes")
            # Verifies against the ephemeral dev key...
            self.assertTrue(integrity._ed25519_verify(
                dev_pub, canonical, sig))
            # ...and does NOT verify against the pinned release key:
            # dev-only by construction, never release-signed.
            self.assertFalse(integrity._ed25519_verify(
                _pinned_public_key(), canonical, sig))
            # The dev key is not the pinned key.
            self.assertNotEqual(dev_pub, _pinned_public_key())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_real_signing_flow_intact(self):
        # The repo's ONE release flow is untouched by this batch:
        # the kit files exist and the signer's crypto round-trips
        # with an ephemeral seed (never the release key).
        for name in ("gen_install_manifest.py", "sign_manifest.py"):
            self.assertTrue((KIT / name).is_file(), f"kit file missing: {name}")
        sys.path.insert(0, str(KIT))
        try:
            signer = importlib.import_module("sign_manifest")
        finally:
            sys.path.remove(str(KIT))
        tmp = Path(tempfile.mkdtemp(prefix="devsign_kit_"))
        try:
            target = tmp / "manifest.json"
            target.write_bytes(b'{"files": {}}')
            seed = os.urandom(32)
            sig_path = signer.write_signature(target, seed)
            self.assertTrue(sig_path.is_file())
            sig = base64.b64decode(sig_path.read_text(
                encoding="utf-8").strip())
            self.assertEqual(len(sig), 64)
            integrity = _load_synth_integrity(TREE)
            self.assertIsNotNone(integrity._ed25519_verify)
            self.assertTrue(integrity._ed25519_verify(
                signer.public_key_from_seed(seed),
                target.read_bytes(), sig))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        # The tree's shipped pair is format-intact (this batch never
        # touches it). No content assertion: the manifest is stale
        # vs the lab tree by design until the v6.3.0 re-sign.
        manifest_path = TREE / "install_manifest.json"
        sig_path = TREE / "install_manifest.json.sig"
        self.assertTrue(manifest_path.is_file())
        self.assertTrue(sig_path.is_file())
        self.assertEqual(len(base64.b64decode(
            sig_path.read_text(encoding="utf-8").strip())), 64)


if __name__ == "__main__":
    unittest.main()
