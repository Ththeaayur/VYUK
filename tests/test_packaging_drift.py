"""GAPS-30 (D-V8): packaging drift check, in-tree.

The drift check used to live only outside the tree
(~/workspace/code-lab/check_packaging.py): hiddenimports in
installer/vyuk.spec must cover every module the frozen app
can reach ONLY through a dynamic import, and
core/catalogue_snapshot.py must equal the live GROUP_MEMBERS
in core/catalogue_groups_data.py. This module ports that
derivation into the tree so the SUITE enforces it too.

Two checks:
1. hiddenimports coverage: re-derives every module reachable
   only via a dynamic import (importlib.import_module with a
   string, __import__ with a variable/f-string, or a loader
   helper called with string literals) and fails if the spec's
   hiddenimports does not cover all of them — and fails on
   DEAD entries too (derived == spec exactly: over-inclusion
   was how the dead sqlcipher3 / pysqlcipher3 entries
   survived, with no load site anywhere).
2. snapshot freshness: the catalogue snapshot must equal the
   live GROUP_MEMBERS (the snapshot is the fallback only;
   drift makes the fallback lie).

derive_dynamic_imports(tree_root) and
spec_hiddenimports(spec_path) take their inputs as arguments
so the batch prover can re-run the same shapes against any
tree (e.g. a pristine backup) without the test harness.
Stdlib only.
"""

import ast
import unittest
from pathlib import Path

TREE_ROOT = Path(__file__).resolve().parent.parent
SPEC = TREE_ROOT / "installer" / "vyuk.spec"

# Static-name dynamic imports whose target is not a real module
# name in the frozen namespace (stdlib, always bundled).
STD_LIB_EXCLUDE = {"os", "math", "sys", "json", "time"}

# Third-party packages loaded only through dynamic forms. They are
# optional (every call site falls back when they are missing), so
# a build without them installed only warns — but listing them
# documents the dependency and bundles them when present.
# (GAPS-30, D-V8: sqlcipher3 / pysqlcipher3 were removed here —
# no load site exists; only the historical leaf name
# bz6_sqlcipher_v572 in a test and an evidence cell in
# core/business_funnel_wiring.py mention them.)
OPTIONAL_THIRD_PARTY = {
    "diskcache", "whenever", "aiohappyeyeballs",  # resilience_swaps
    "superqt",  # gui/superqt_widgets probe
    "pywinctl", "pymonctl",  # B-D1 (2026-11-09): window control —
    # core/desktop/pywinctl_wrapper.py lazy-imports both; optional
    # (every call site degrades to engine_unavailable when missing)
}


def _package_for(path: Path, tree_root: Path) -> str:
    """Source file -> frozen package prefix (voice_assistant.*)."""
    rel = path.relative_to(tree_root)
    parts = rel.parts[:-1]
    if not parts:
        return "voice_assistant"
    return "voice_assistant." + ".".join(parts)


def _canonical(name: str) -> str:
    """Normalize a dynamically-imported name to frozen form."""
    if name.startswith("voice_assistant."):
        return name
    if name.startswith("core."):
        return "voice_assistant." + name
    if name.startswith("gui."):
        return "voice_assistant." + name
    return name


def _collect_call_literals(tree: ast.AST, func_name: str) -> set[str]:
    """String literals passed to calls of func_name anywhere."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            fname = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else "")
            if fname == func_name and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(
                        arg.value, str):
                    out.add(arg.value)
    return out


def derive_dynamic_imports(tree_root) -> set[str]:
    """Every module name reachable only via a dynamic import."""
    tree_root = Path(tree_root)
    found: set[str] = set()
    sources = [tree_root / "main.py", tree_root / "doctor.py"]
    sources += sorted((tree_root / "core").rglob("*.py"))
    sources += sorted((tree_root / "gui").rglob("*.py"))
    for path in sources:
        if not path.exists():
            continue
        try:
            parsed = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        pkg = _package_for(path, tree_root)
        # 1. import_module/__import__ with a plain string literal.
        for node in ast.walk(parsed):
            if isinstance(node, ast.Call):
                f = node.func
                fname = f.attr if isinstance(f, ast.Attribute) else (
                    f.id if isinstance(f, ast.Name) else "")
                if fname in ("import_module", "__import__") and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(
                            arg.value, str):
                        name = arg.value
                        if name not in STD_LIB_EXCLUDE:
                            found.add(_canonical(name))
        # 2. __import__ over a for-loop tuple of string literals
        #    (tools_handlers pack loops).
        for node in ast.walk(parsed):
            if isinstance(node, ast.For) and isinstance(node.iter,
                                                        ast.Tuple):
                try:
                    vals = [ast.literal_eval(e)
                            for e in node.iter.elts]
                except Exception:
                    continue
                if not vals or not all(isinstance(v, str)
                                       for v in vals):
                    continue
                body = ast.dump(node)
                if "__import__" in body and "module_name" in body:
                    for v in vals:
                        found.add(f"{pkg}.{v}")
        # 3. Loader helpers called with string literals.
        for lit in _collect_call_literals(parsed, "_load_rl"):
            found.add(f"voice_assistant.core.resilience.{lit}")
        for lit in _collect_call_literals(parsed, "_load_optional"):
            found.add(lit)
        # 4. resilience_gate path forms.
        for node in ast.walk(parsed):
            if isinstance(node, ast.For) and isinstance(node.iter,
                                                        ast.Tuple):
                try:
                    vals = [ast.literal_eval(e)
                            for e in node.iter.elts]
                except Exception:
                    continue
                if vals and all(isinstance(v, str) for v in vals) \
                        and "resilience_status" in ast.dump(node):
                    found.add("voice_assistant.gui.resilience_status")
        # 5. doctor's _MODULES list feeding import_module(f"...{mod}").
        for node in ast.walk(parsed):
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "_MODULES"
                    for t in node.targets):
                try:
                    mods = ast.literal_eval(node.value)
                except Exception:
                    continue
                if isinstance(mods, list):
                    for m in mods:
                        found.add(f"voice_assistant.{m}")
    # 6. Optional third-party dynamic loads (pin the full known
    #    set so a future refactor that moves the literal out of
    #    a call cannot silently drop one).
    found |= OPTIONAL_THIRD_PARTY
    return {n for n in found if n and all(
        part.isidentifier() for part in n.split("."))}


def spec_hiddenimports(spec_path) -> set[str]:
    """hiddenimports list parsed from installer/vyuk.spec."""
    parsed = ast.parse(Path(spec_path).read_text(encoding="utf-8"))
    for node in ast.walk(parsed):
        if isinstance(node, ast.Call):
            fname = node.func.id if isinstance(node.func, ast.Name) \
                else ""
            if fname == "Analysis":
                for kw in node.keywords:
                    if kw.arg == "hiddenimports" and isinstance(
                            kw.value, ast.List):
                        return {e.value for e in kw.value.elts
                                if isinstance(e, ast.Constant)}
    raise AssertionError(f"hiddenimports= not found in {spec_path}")


def _group_members(path: Path) -> dict:
    parsed = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(parsed):
        target = getattr(node, "target", None)
        if target is None and isinstance(node, ast.Assign) \
                and len(node.targets) == 1:
            target = node.targets[0]
        if target is not None and getattr(target, "id", "") == \
                "GROUP_MEMBERS" and node.value is not None:
            found = ast.literal_eval(node.value)
            if isinstance(found, dict):
                return {k: tuple(v) for k, v in found.items()}
    raise AssertionError(f"GROUP_MEMBERS not found in {path}")


def live_groups(tree_root) -> dict:
    return _group_members(
        Path(tree_root) / "core" / "catalogue_groups_data.py")


def snapshot_groups(tree_root) -> dict:
    return _group_members(
        Path(tree_root) / "core" / "catalogue_snapshot.py")


class PackagingDriftTests(unittest.TestCase):
    def test_hiddenimports_match_derived_exactly(self):
        derived = derive_dynamic_imports(TREE_ROOT)
        have = spec_hiddenimports(SPEC)
        self.assertEqual(derived, have,
                         f"missing from spec: "
                         f"{sorted(derived - have)}; dead in spec: "
                         f"{sorted(have - derived)}")

    def test_no_dead_sqlcipher_entries(self):
        have = spec_hiddenimports(SPEC)
        self.assertNotIn("sqlcipher3", have)
        self.assertNotIn("pysqlcipher3", have)
        self.assertNotIn("sqlcipher3", OPTIONAL_THIRD_PARTY)
        self.assertNotIn("pysqlcipher3", OPTIONAL_THIRD_PARTY)

    def test_catalogue_snapshot_fresh(self):
        self.assertEqual(live_groups(TREE_ROOT),
                         snapshot_groups(TREE_ROOT),
                         "regenerate with gen_catalogue_snapshot.py")


if __name__ == "__main__":
    unittest.main()
