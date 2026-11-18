"""GAPS-30 (T-V8): the import-graph layering rule, enforced generally.

Before this batch the only import-graph enforcement in the tree
was a one-off: tests/test_kokoro_isolate.py forbids the Kokoro
GPL engine names in the main process. The layering itself —
core/ never imports gui/, and gui/ reaches core/ only through
the facade — was prose, not a test. This module states the
rule once and enforces it over the whole tree by AST:

  1. core/ never imports gui/ (absolute or relative imports).
  2. gui/ never imports core.* directly: the GUI reaches core
     ONLY through core.gui_facade (the re-export surface),
     under either spelling (core.gui_facade or the full
     package path voice_assistant.core.gui_facade) and
     either form (absolute or `from ..core.gui_facade`).
     Docstring mentions of core.funnel are not imports and
     are out of this rule by construction.
  3. The Kokoro GPL one-off is one case of the same rule: the
     only kokoro_onnx importer in the tree is the child entry
     point itself (core/desktop/kokoro_child.py), and nobody
     imports the phoneme stack directly at all.

check_tree(tree_root) returns the violation list (empty =
the tree honours the rule) so the batch prover can re-run
the same shapes without the test harness. The scan is
cached per tree root: the four tests below share one scan.
"""

import ast
import unittest
from functools import lru_cache
from pathlib import Path

TREE_ROOT = Path(__file__).resolve().parent.parent

# The GPL engine names (tests/test_kokoro_isolate.py FORBIDDEN).
FORBIDDEN = ("kokoro_onnx", "phonemizer", "espeakng_loader",
             "espeak_ng", "espeak")


def _top(module: str) -> str:
    return module.split(".")[0] if module else ""


def _is_gui_module(module: str) -> bool:
    return _top(module) == "gui" \
        or module.startswith("voice_assistant.gui")


def _is_core_module(module: str) -> bool:
    return _top(module) == "core" \
        or module.startswith("voice_assistant.core")


def _is_facade(module: str) -> bool:
    """The facade under either spelling."""
    return module.removeprefix("voice_assistant.") \
        == "core.gui_facade"


def _resolve_relative(path: Path, root: Path, node) -> str:
    """Resolve a relative ImportFrom to its absolute dotted module.

    E.g. `from ..core.gui_facade import x` in gui/changelog_widget.py
    (package gui) resolves to core.gui_facade — the facade under
    the top-level spelling; _is_facade accepts both spellings.
    A relative import that reached core under any other name
    (e.g. `..core.memory`) would resolve to core.memory and be
    flagged by rule 2.
    """
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts[:-1])  # the file's package, dotted
    up = node.level - 1
    base = parts[:len(parts) - up] if up <= len(parts) else []
    mod = node.module or ""
    return ".".join(base + ([mod] if mod else []))


@lru_cache(maxsize=8)
def _scan_cached(root_str: str) -> tuple:
    root = Path(root_str)
    violations: list[str] = []

    def parse(path: Path):
        try:
            return ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            violations.append(f"{path.relative_to(root)}: unparsable")
            return None

    def _imported_modules(path: Path, tree):
        """Yield every module an Import/ImportFrom resolves to."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0:
                    if node.module:
                        yield node.module
                else:
                    yield _resolve_relative(path, root, node)

    # Rule 1: core/ never imports gui/.
    for path in sorted((root / "core").rglob("*.py")):
        tree = parse(path)
        if tree is None:
            continue
        for module in _imported_modules(path, tree):
            if _is_gui_module(module):
                violations.append(
                    f"{path.relative_to(root)}: core imports "
                    f"gui ({module})")

    # Rule 2: gui/ reaches core/ only through core.gui_facade.
    for path in sorted((root / "gui").rglob("*.py")):
        tree = parse(path)
        if tree is None:
            continue
        for module in _imported_modules(path, tree):
            if _is_core_module(module) and not _is_facade(module):
                violations.append(
                    f"{path.relative_to(root)}: gui imports core "
                    f"directly ({module}) — use core.gui_facade")

    # Rule 3: the Kokoro GPL one-off, as one case of the rule.
    found = {name: set() for name in FORBIDDEN}
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in found:
                        found[top].add(str(path.relative_to(root)))
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top = node.module.split(".")[0]
                    if top in found:
                        found[top].add(str(path.relative_to(root)))
    if found["kokoro_onnx"] != {"core/desktop/kokoro_child.py"}:
        violations.append(
            "kokoro_onnx importers are not exactly the child: "
            f"{sorted(found['kokoro_onnx'])}")
    for name in ("phonemizer", "espeakng_loader", "espeak_ng",
                 "espeak"):
        if found[name]:
            violations.append(
                f"{name} imported directly by "
                f"{sorted(found[name])}")
    return tuple(violations)


def check_tree(tree_root) -> list[str]:
    """Return the layering violations in the tree (empty = clean)."""
    return list(_scan_cached(str(Path(tree_root).resolve())))


class ImportGraphTests(unittest.TestCase):
    def test_layering_rule_holds(self):
        self.assertEqual(check_tree(TREE_ROOT), [])

    def test_core_never_imports_gui(self):
        self.assertEqual(
            [v for v in check_tree(TREE_ROOT)
             if "core imports gui" in v], [])

    def test_gui_reaches_core_only_via_facade(self):
        self.assertEqual(
            [v for v in check_tree(TREE_ROOT)
             if "gui imports core directly" in v], [])

    def test_kokoro_rule_is_one_case_of_the_layering_rule(self):
        self.assertEqual(
            [v for v in check_tree(TREE_ROOT)
             if "kokoro_onnx" in v or "phonemizer" in v
             or "espeak" in v], [])


if __name__ == "__main__":
    unittest.main()
