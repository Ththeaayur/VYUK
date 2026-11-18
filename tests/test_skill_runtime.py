"""Contained runtime pilot (B8, SK4-05): Wasmtime WASM skill runs.

A skill's code runs ONLY inside Wasmtime (no WASI, no host
functions, empty imports, fuel-bounded) after the gate admits it,
its ``wasm:`` capability is declared, and its signed manifest
verifies against a trusted key. Every other shape is refused
before anything executes, and the refusal is recorded.

The pilot skill is built here from embedded WAT: ``run`` takes
two i32 and returns their sum plus 41, so run(1, 2) == 44 — a
known input gives a known output.
"""

import ast
import hashlib
import json
import os
import sys
import tempfile
import types as _types_mod
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wasmtime

from core import skill_runtime as sr
from core import skill_signing as ss

_VA_SNAPSHOT = None


def setUpModule():
    """Mirror test_mic_first_use_ask's voice_assistant.* alias.

    core/personal_skills.py imports voice_assistant.core.*
    (pre-split paths); the tree layout is top-level core/ + gui/,
    so the alias maps voice_assistant -> tree root. Without it
    the module falls back to a file-path load of skill_format
    that crashes on its dataclass (sys.modules has no entry for
    it). Snapshot discipline: restore prior entries on teardown
    so a co-run suite's cached modules survive.
    """
    global _VA_SNAPSHOT
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core


def tearDownModule():
    global _VA_SNAPSHOT
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        del sys.modules[key]
    if _VA_SNAPSHOT is not None:
        sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None

ADD_WAT = """(module
  (func (export "run") (param i32 i32) (result i32)
    local.get 0
    local.get 1
    i32.add
    i32.const 41
    i32.add))"""

IMPORT_WAT = """(module
  (import "wasi_snapshot_preview1" "fd_write"
    (func (param i32 i32 i32 i32) (result i32)))
  (func (export "run") (param i32 i32) (result i32)
    i32.const 0))"""

LOOP_WAT = """(module
  (func (export "run") (param i32 i32) (result i32)
    (loop (br 0))
    i32.const 0))"""

NO_RUN_WAT = """(module
  (func (export "other") (param i32) (result i32)
    local.get 0))"""

WRONG_SIG_WAT = """(module
  (func (export "run") (param i64) (result i32)
    i32.const 0))"""

SKILL_MD = """---
name: add-forty-one
description: Adds two numbers and forty-one, inside the contained runtime.
metadata:
  vyuk-capabilities: "wasm:skill.wasm"
---
# Add forty-one

Adds two numbers and forty-one.
"""


def _compile(wat: str) -> bytes:
    return bytes(wasmtime.wat2wasm(wat))


class _Fixture:
    """One skill directory + trust store + kill list in a temp dir."""

    def __init__(self, wat=ADD_WAT, declare=True, sign=True,
                 manifest_text=None, tamper_after_sign=False):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.skill_dir = self.root / "add-forty-one"
        self.skill_dir.mkdir()
        text = SKILL_MD if manifest_text is None else manifest_text
        if not declare:
            text = text.replace(
                '  vyuk-capabilities: "wasm:skill.wasm"\n', "")
        (self.skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
        (self.skill_dir / "skill.wasm").write_bytes(_compile(wat))
        self.seed = os.urandom(32)
        self.public_key = ss.public_key_from_seed(self.seed)
        self.key_id = ss.key_id_for_public_key(self.public_key)
        self.trust_store = self.root / "skill_trust_store.json"
        self.kill_list = self.root / "skill_kill_list.json"
        self.kill_list.write_text("{}\n", encoding="utf-8")
        if sign:
            ss.write_signed_manifest(self.skill_dir, self.seed)
            self.trust_store.write_text(json.dumps({
                self.key_id: {"trusted": True, "reason": "test",
                              "public_key_hex": self.public_key.hex()},
            }), encoding="utf-8")
            if tamper_after_sign:
                (self.skill_dir / "skill.wasm").write_bytes(
                    _compile(ADD_WAT) + b"\x00")
        else:
            self.trust_store.write_text("{}\n", encoding="utf-8")

    def run(self, inputs=(1, 2), **kwargs):
        kwargs.setdefault("trust_store_path", self.trust_store)
        kwargs.setdefault("kill_list_path", self.kill_list)
        return sr.run_wasm_skill(self.skill_dir, list(inputs), **kwargs)

    def cleanup(self):
        self.tmp.cleanup()


class ContainedRuntimeTests(unittest.TestCase):
    def setUp(self):
        sr.clear_refusal_log()

    def test_runtime_available(self):
        self.assertTrue(sr.runtime_available())

    def test_signed_declared_skill_runs_and_returns_known_output(self):
        fx = _Fixture()
        try:
            result = fx.run()
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["result"], 44)
            self.assertEqual(result["wasm_file"], "skill.wasm")
            self.assertEqual(
                result["module_sha256"],
                hashlib.sha256(
                    (fx.skill_dir / "skill.wasm").read_bytes()
                ).hexdigest())
            self.assertIn("elapsed_s", result)
            self.assertGreaterEqual(result["elapsed_s"], 0)
        finally:
            fx.cleanup()

    def test_run_is_deterministic(self):
        fx = _Fixture()
        try:
            self.assertEqual(fx.run((3, 4))["result"], 48)
            self.assertEqual(fx.run((3, 4))["result"], 48)
        finally:
            fx.cleanup()

    def test_undeclared_skill_is_refused_before_it_runs(self):
        fx = _Fixture(declare=False)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "wasm_not_declared")
            codes = [r["code"] for r in sr.refusal_log()]
            self.assertIn("wasm_not_declared", codes)
        finally:
            fx.cleanup()

    def test_declared_but_missing_module_is_refused(self):
        fx = _Fixture()
        try:
            (fx.skill_dir / "skill.wasm").unlink()
            # Re-sign without the module so the signature still verifies.
            ss.write_signed_manifest(fx.skill_dir, fx.seed)
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "wasm_missing_file")
        finally:
            fx.cleanup()

    def test_two_wasm_entries_refused(self):
        text = SKILL_MD.replace(
            '"wasm:skill.wasm"',
            '"wasm:skill.wasm; wasm:other.wasm"')
        fx = _Fixture(manifest_text=text)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "wasm_bad_target")
        finally:
            fx.cleanup()

    def test_path_traversal_target_refused(self):
        text = SKILL_MD.replace(
            '"wasm:skill.wasm"', '"wasm:../evil.wasm"')
        fx = _Fixture(manifest_text=text)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "wasm_bad_target")
        finally:
            fx.cleanup()

    def test_unsigned_skill_is_refused(self):
        fx = _Fixture(sign=False)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "no_trusted_key")
        finally:
            fx.cleanup()

    def test_wrong_trusted_key_is_refused(self):
        fx = _Fixture()
        try:
            other_seed = os.urandom(32)
            other_pub = ss.public_key_from_seed(other_seed)
            other_id = ss.key_id_for_public_key(other_pub)
            fx.trust_store.write_text(json.dumps({
                other_id: {"trusted": True, "reason": "test",
                           "public_key_hex": other_pub.hex()},
            }), encoding="utf-8")
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "signature_unverified")
        finally:
            fx.cleanup()

    def test_tampered_module_is_refused(self):
        fx = _Fixture(tamper_after_sign=True)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "signature_unverified")
        finally:
            fx.cleanup()

    def test_revoked_key_is_refused_by_the_gate(self):
        fx = _Fixture()
        try:
            fx.kill_list.write_text(json.dumps(
                {fx.key_id: {"reason": "test"}}), encoding="utf-8")
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "gate_refused")
        finally:
            fx.cleanup()

    def test_module_needing_host_imports_is_refused(self):
        fx = _Fixture(wat=IMPORT_WAT)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "import_required")
        finally:
            fx.cleanup()

    def test_infinite_loop_is_stopped_by_fuel(self):
        fx = _Fixture(wat=LOOP_WAT)
        try:
            result = fx.run(fuel=1000)
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "fuel_exhausted")
        finally:
            fx.cleanup()

    def test_missing_run_export_is_refused(self):
        fx = _Fixture(wat=NO_RUN_WAT)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "bad_abi")
        finally:
            fx.cleanup()

    def test_wrong_run_signature_is_refused(self):
        fx = _Fixture(wat=WRONG_SIG_WAT)
        try:
            result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "bad_abi")
        finally:
            fx.cleanup()

    def test_non_integer_inputs_are_refused(self):
        fx = _Fixture()
        try:
            result = sr.run_wasm_skill(
                fx.skill_dir, ["1", 2],
                trust_store_path=fx.trust_store,
                kill_list_path=fx.kill_list)
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "bad_abi")
        finally:
            fx.cleanup()

    def test_runtime_unavailable_is_honest_never_faked(self):
        fx = _Fixture()
        try:
            with mock.patch.dict(sys.modules, {"wasmtime": None}):
                result = fx.run()
            self.assertEqual(result["status"], "refused")
            self.assertEqual(result["code"], "runtime_unavailable")
        finally:
            fx.cleanup()

    def test_skill_code_never_runs_in_process(self):
        """Static pin: the runtime module has no host-exec calls."""
        source = (Path(__file__).resolve().parents[1]
                  / "core" / "skill_runtime.py").read_text(
                      encoding="utf-8")
        tree = ast.parse(source)
        banned = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in (
                        "eval", "exec", "compile", "__import__"):
                    banned.add(func.id)
                if isinstance(func, ast.Attribute) and func.attr in (
                        "system", "popen", "run", "call", "check_output"):
                    banned.add(func.attr)
        self.assertEqual(banned, set())
        self.assertNotIn("subprocess", source)
        self.assertNotIn("os.system", source)


class WiringRunTests(unittest.TestCase):
    """The funnel wiring's run action: admitted only, contained only."""

    def test_run_end_to_end_through_the_wiring(self):
        from core import personal_skills as ps
        from core import skills_tool_wiring as sw

        fx = _Fixture()
        try:
            lib_root = fx.root / "library"
            lib_root.mkdir()
            skill_lib = lib_root / "add-forty-one"
            import shutil
            shutil.copytree(fx.skill_dir, skill_lib)
            ps.PersonalLibrary(root=lib_root)._upsert_entry(
                {"name": "add-forty-one", "state": "draft"})
            admit = sw.personal_skills_tool(
                {"action": "admit", "name": "add-forty-one"},
                library_root=lib_root)
            self.assertTrue(admit["ok"], admit)
            run = sw.personal_skills_tool(
                {"action": "run", "name": "add-forty-one",
                 "inputs": "[1, 2]"},
                library_root=lib_root,
                trust_store_path=fx.trust_store,
                kill_list_path=fx.kill_list)
            self.assertTrue(run["ok"], run)
            self.assertEqual(run["result"], 44)
            self.assertEqual(run["status"], "ok")
        finally:
            fx.cleanup()

    def test_run_refuses_a_draft(self):
        from core import skills_tool_wiring as sw

        fx = _Fixture()
        try:
            lib_root = fx.root / "library"
            lib_root.mkdir()
            import shutil
            shutil.copytree(fx.skill_dir, lib_root / "add-forty-one")
            # Register the draft in the library index without admitting.
            from core import personal_skills as ps
            lib = ps.PersonalLibrary(root=lib_root)
            lib._upsert_entry({"name": "add-forty-one",
                               "state": "draft"})
            run = sw.personal_skills_tool(
                {"action": "run", "name": "add-forty-one",
                 "inputs": "[1, 2]"},
                library_root=lib_root,
                trust_store_path=fx.trust_store,
                kill_list_path=fx.kill_list)
            self.assertFalse(run["ok"])
            self.assertIn("not admitted", run["error"])
        finally:
            fx.cleanup()

    def test_run_rejects_unparseable_inputs(self):
        from core import skills_tool_wiring as sw

        fx = _Fixture()
        try:
            run = sw.personal_skills_tool(
                {"action": "run", "name": "add-forty-one",
                 "inputs": "not-json"},
                library_root=fx.root,
                trust_store_path=fx.trust_store,
                kill_list_path=fx.kill_list)
            self.assertFalse(run["ok"])
        finally:
            fx.cleanup()


class GateIntegrationTests(unittest.TestCase):
    def test_run_needs_confirmation_and_is_tier_2(self):
        from core.funnel import tools_gate as tg
        from core.funnel import tools_gate_tier as tt
        from core.funnel import tools_gate_readclass as tr

        self.assertTrue(tg._needs_confirmation(
            "personal_skills", {"action": "run"}))
        self.assertTrue(tg._needs_confirmation(
            "personal_skills", {"action": "admit"}))
        self.assertFalse(tg._needs_confirmation(
            "personal_skills", {"action": "list"}))
        self.assertEqual(tt._tool_tier(
            "personal_skills", {"action": "run"}), 2)
        self.assertEqual(tt._tool_tier(
            "personal_skills", {"action": "list"}), 0)
        self.assertFalse(tr._is_read_action(
            "personal_skills", {"action": "run"}))

    def test_wasm_scope_is_signature_tier(self):
        from core import skill_format as sf
        from core.skill_gate_grants import evaluate_grants

        self.assertIn("wasm", sf.MANIFEST_SCOPES)
        self.assertIn("wasm", sf.SIGNATURE_TIER_SCOPES)
        verdict = evaluate_grants(["wasm:skill.wasm"],
                                  signed_by_vyuk=False)
        self.assertEqual(verdict["granted"], [])
        self.assertEqual(len(verdict["refused"]), 1)
        signed = evaluate_grants(["wasm:skill.wasm"],
                                 signed_by_vyuk=True)
        self.assertEqual(signed["granted"], ["wasm:skill.wasm"])

    def test_manifest_parser_accepts_wasm_entry(self):
        from core import skill_format as sf

        manifest, findings = sf.parse_capability_manifest(
            {"vyuk-capabilities": "wasm:skill.wasm"})
        self.assertEqual(findings, [])
        self.assertEqual(
            [(e["scope"], e["target"])
             for e in manifest["effective"]],
            [("wasm", "skill.wasm")])
        self.assertEqual(
            [(e["scope"], e["target"])
             for e in manifest["signature_tier"]],
            [("wasm", "skill.wasm")])


if __name__ == "__main__":
    unittest.main()
