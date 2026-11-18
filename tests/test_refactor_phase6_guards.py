"""Refactor Phase 6 guards — lock the Phase 3/4 structure in place.

These suites are TESTS and CHECKERS, never product behaviour: they assert
about the tree's shape (paths, imports, package layout), never about what
the product does. Each guard cites the Phase 3/4 lesson or invariant it
locks; nothing here adds product policy, a confirm gate, or a ban list
beyond the refactor's own invariants (Phase 3 plan section 6: no new
policy in the refactor — guards lock what the refactor did).

Derived scope (Phase 6 audit, 2026-11-03; no dedicated Phase 6 plan doc
exists, so the scope below IS the derived scope):

* Phase 4 deletion guard — the 7 deleted paths must stay deleted, by
  file presence AND by importability (a file reappearing under a new
  name that still imports as the old dotted path would defeat a
  path-only check).
* Package-boundary guard — the corrected hub-import rule (Phase 3):
  ``from core import X`` from OUTSIDE binds the PACKAGE, not the hub,
  so outside importers must use the explicit submodule path
  (``core.<pkg>.X``); and ``core.tools`` must not be imported anywhere
  (Phase 4 S1c deleted the shim; S1a/S1b moved every importer).
  Covered forms: static imports (all levels, absolute + relative),
  constructed import_module/__import__ string literals, patch-target
  strings, and the flat pack-loop tuples. NOT covered, disclosed:
  f-string constructed imports (other than the flat pack loops, whose
  tuples ARE checked); sys.modules stub keys in tests (they plant
  test-local fakes, they do not load old paths); ``patch.object``
  with a non-string target (no module path to check).
* Same-stem collision guard — the M1a lesson: a flat module and a
  package must never share a stem again (flat ``core/desktop.py`` vs
  ``core/desktop/`` broke the S7a-2 package creation the same way
  flat ``core/brain.py`` would have broken B1a).
* Package-structure guard — the Phase 3 package rules: every Phase 3
  package exists with a docstring-only ``__init__.py`` (plan section
  3.5); every member parses; the two flat pack files stay flat
  (plan section 5.3: moving them rebases their tuple resolution and
  skill loading silently drops to zero); the import-time edge
  directions of plan section 5.1 hold (desktop -> funnel is zero;
  brain -> funnel is ONLY the planner's tool_result import, whose
  cycle-free justification — tool_result is stdlib-only — is itself
  asserted); the funnel's import-time graph is acyclic.
* Packaging-gap guard — check_packaging.py already derives the
  dynamic set from the moved tree (rglob + package-from-path), so no
  pin update was needed in Phase 6; this guard locks its outcome:
  the ONLY allowed missing hiddenimport is the pre-existing
  ``voice_assistant.core.w8_wiring`` gap (a Phase 2 rename leftover,
  deliberately NOT fixed here — a spec change is out of Phase 6
  scope), and the catalogue snapshot must stay fresh. This differs
  deliberately from test_packaging's strict assertion (missing ==
  empty), which is the known leaver for exactly that gap.

Package-membership counts are RECORDED here, not asserted (Phase 6
brief, item d): counts legitimately change with feature batches
(the §7 consumers batch added a funnel pack module), so an exact
count pin would fail on the next legitimate batch. Baseline at
Phase 6 implementation (modules excluding __init__.py): resilience 10,
health 6, desktop 57, memory 15, brain 17, voice 20, funnel 37
(38 before Phase 4 deleted core/funnel/tools_gates.py) — 162 total,
which is Phase 3's 176 moved files minus exactly those 2 Phase 4
deletions and the 12 world files removed with the world-view
feature (2026-11-05). If a future batch moves a module, it updates
this paragraph with an INTENTIONAL comment, never silently.

Root-runner section (lab layout only): the in-tree scope above is
the shipped tree. The code-lab root runners are harness files, not
shipped code; their core.tools imports are already zero (Phase 4
shim re-baseline, AST gate) and this suite re-asserts that zero
whenever the lab layout is present. Their flat packaged imports
are a disclosed backlog, NOT silently re-pinned here: 70 lines in
41 non-battery root runners (old v32–v59/v554–v570 harness files
the battery does not run). This suite asserts no NEW root-runner
file gains a flat import (the file set must stay within the known
stale set below); growth inside an already-stale file is the
disclosed gap, and the migration that clears the backlog is a
separate harness batch, not a guard edit. When the lab layout is
absent (a shipped install has no code-lab root), the root-runner
test skips, exactly like test_packaging's checker test.

Stdlib unittest only. Run via tests/run_tests.py or alone on both
interpreters (PYTHONPATH=., PYTHONDONTWRITEBYTECODE=1).
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent

# Phase 3 packages (plan section 1; platform/ predates Phase 3 and is
# covered by the generic twin check, not listed here).
# INTENTIONAL (world-view removal, 2026-11-05): "world" left
# PHASE3_PACKAGES with the feature — core/world/ is deleted.
PHASE3_PACKAGES = (
    "resilience", "health", "desktop",
    "memory", "brain", "voice", "funnel",
)

# Phase 4 deletions: (on-disk path relative to TREE, dotted name).
# core/tools.py is the S1c shim deletion; the other six are D1/D2.
DELETED_MODULES = (
    ("core/fake_eval.py", "voice_assistant.core.fake_eval"),
    ("core/brain_eval_rig.py", "voice_assistant.core.brain_eval_rig"),
    ("core/c14_rulings.py", "voice_assistant.core.c14_rulings"),
    ("core/briefing.py", "voice_assistant.core.briefing"),
    ("core/funnel/tools_gates.py", "voice_assistant.core.funnel.tools_gates"),
    ("core/world/globe_bench.py", "voice_assistant.core.world.globe_bench"),
    ("core/tools.py", "voice_assistant.core.tools"),
)

# The two flat pack files (plan section 5.3: packs stay flat).
FLAT_PACK_FILES = (
    "core/tools_handlers_pack_hands.py",
    "core/tools_handlers_pack_waves.py",
)

# Pre-existing packaging gap, deliberately preserved (see docstring).
ALLOWED_PACKAGING_GAP = {"voice_assistant.core.w8_wiring"}

# INTENTIONAL (guards follow-up batch, 2026-11-05): named-file-only
# exception for the flat-imports guard. tests/test_memorystore_export.py
# MUST import the core.memory package flat — that is what it verifies
# (the package-level MemoryStore export, added by the MemoryStore
# export batch 2026-11-05 for root scripts test_v46_batch3b.py and
# test_v52_episodes.py). The exception is one (file, import) pair;
# any other file, or any other import in this file, still fails.
ALLOWED_FLAT_IMPORTS = frozenset({
    ("tests/test_memorystore_export.py", "core.memory"),
})

# INTENTIONAL (guards follow-up batch, 2026-11-05): named-package-only
# exception for the docstring-only __init__ rule. core/memory/__init__.py
# carries exactly one re-export (from .memory import MemoryStore) plus
# __all__ = ["MemoryStore"], added by the MemoryStore export batch
# 2026-11-05 for root scripts test_v46_batch3b.py and
# test_v52_episodes.py. Safe by shape: memory.py imports stdlib +
# ..config only, so the re-export cannot cycle (it imports nothing
# from the package itself) and has no side effects beyond binding one
# name. The test below enforces that exact shape for allowlisted
# packages — any other statement still offends. Note: platform is NOT
# in PHASE3_PACKAGES, so its code-heavy __init__ is outside this test
# entirely; the memory exception is the only one.
ALLOWED_INIT_REEXPORTS = {
    # package -> (relative module, re-exported name)
    "memory": ("memory", "MemoryStore"),
}

# Root-runner flat-import backlog (Phase 6 audit, 2026-11-03): this set
# held the 41 non-battery root-runner files that already contained a
# flat import of a packaged stem (70 lines total). New files must not
# join this set; clearing it is a separate harness migration, not a
# guard edit. Phase 6 INTENTIONAL: recorded from the audit, never
# hand-edited.
# INTENTIONAL (guards follow-up batch, 2026-11-05): CLEARED WHOLESALE
# per parent ruling. The guard's own measure is met — the migration
# scan proves tools_hits == [] and flat_files == [] (no root-runner
# file currently contains a flat packaged import by the guard's
# definition, and none of the 41 formerly-listed files is still flat),
# so emptying the set keeps test_root_runners_locked green AND makes
# the lock STRICTER: this set is an allowance list (new_files =
# flat_files minus the set), so a future regression in any of the 41
# files now FAILS loudly instead of passing silently inside the
# allowance. The migration's downstream blocks (version modules,
# registry drift, platform seams, PortAudio) are runnability grounds
# the guard does not measure and are not a reason to hold import
# exemptions. The pre-clear list is preserved verbatim in the batch
# backup (backup_guards_followup_pre/tests/test_refactor_phase6_guards.py).
KNOWN_STALE_ROOT_FILES = frozenset()

_PATCH_RE = re.compile(
    r"""patch(?:\.object|\.multiple)?\(\s*["']([^"']+)["']""")

_SCAN_CACHE: dict = {}


def _clear_scan_cache():
    _SCAN_CACHE.clear()


def _tree_py_files(tree: Path):
    out = []
    for dirpath, dirnames, filenames in os.walk(tree):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                out.append(Path(dirpath) / name)
    return sorted(out)


def _packaged_stems(tree: Path):
    """stem -> package for every Phase 3 package member on disk."""
    stems = {}
    for pkg in PHASE3_PACKAGES:
        pkgdir = tree / "core" / pkg
        if not pkgdir.is_dir():
            continue
        for name in os.listdir(pkgdir):
            if name.endswith(".py") and name != "__init__.py":
                stems[name[:-3]] = pkg
    return stems


def _resolve_from(tree: Path, path: Path, level: int, module):
    """Resolve an ImportFrom to (base_parts, name_parts) for the guard.

    Returns the resolved package path parts (without the imported
    name) for absolute and relative forms. ``voice_assistant`` is
    stripped from absolute paths; relative levels resolve against
    the importing file's directory parts relative to TREE.
    """
    rel = path.relative_to(tree)
    dir_parts = list(rel.parts[:-1])
    if level == 0:
        base = (module or "").replace("voice_assistant.", "").split(".") \
            if module else []
        base = [p for p in base if p]
        return base
    # level 1 = current package, level 2 = its parent, ...
    cut = len(dir_parts) - (level - 1)
    base = dir_parts[:cut] if cut >= 0 else []
    if module:
        base = base + module.split(".")
    return base


def _scan_boundary(tree: Path):
    """One AST pass over the tree; returns the violation lists.

    Cached per tree path (the suite methods share one scan).
    """
    key = str(tree)
    if key in _SCAN_CACHE:
        return _SCAN_CACHE[key]
    stems = _packaged_stems(tree)
    static_tools = []   # core.tools imports, any form
    flat_imports = []   # flat imports of packaged stems
    constructed = []    # import_module/__import__ literals, old paths
    patch_targets = []  # patch-target strings, old paths
    pack_tuples = []    # flat pack-loop tuples naming packaged stems
    for path in _tree_py_files(tree):
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            parsed = ast.parse(src)
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(tree))
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.replace("voice_assistant.", "").split(".")
                    if parts == ["core", "tools"]:
                        static_tools.append((rel, node.lineno, alias.name))
                    elif len(parts) == 2 and parts[0] == "core" \
                            and parts[1] in stems:
                        flat_imports.append((rel, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                base = _resolve_from(tree, path, node.level, node.module)
                for alias in node.names:
                    if base == ["core", "tools"]:
                        static_tools.append((rel, node.lineno,
                                             f"from {node.module} import {alias.name}"))
                    elif alias.name == "*":
                        continue
                    elif base == ["core"] and alias.name == "tools":
                        static_tools.append((rel, node.lineno,
                                             f"from {node.module} import tools"))
                    elif base == ["core"] and alias.name in stems:
                        flat_imports.append((rel, node.lineno,
                                             f"from {node.module} import {alias.name}"))
            elif isinstance(node, ast.Call):
                func = node.func
                fname = func.attr if isinstance(func, ast.Attribute) else (
                    func.id if isinstance(func, ast.Name) else "")
                if fname in ("import_module", "__import__") and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        parts = arg.value.replace("voice_assistant.", "").split(".")
                        if parts == ["core", "tools"]:
                            constructed.append((rel, node.lineno, arg.value))
                        elif len(parts) == 2 and parts[0] == "core" \
                                and parts[1] in stems:
                            constructed.append((rel, node.lineno, arg.value))
        # Patch-target strings (text scan: patch targets are strings,
        # not AST imports, so the AST pass cannot see their meaning).
        for match in _PATCH_RE.finditer(src):
            target = match.group(1)
            parts = target.replace("voice_assistant.", "").split(".")
            # A second component naming a PACKAGE is the new
            # packaged form (core.<package>.<module>.<attr>), not
            # an old flat path: core/desktop/desktop.py puts
            # "desktop" in stems, so core.desktop.* targets would
            # otherwise trip this check. A flat old path is
            # core.<stem> where <stem> is a stem but not a package.
            if len(parts) >= 2 and parts[0] == "core" \
                    and parts[1] not in PHASE3_PACKAGES and (
                    parts[1] == "tools" or parts[1] in stems):
                patch_targets.append((rel, target))
        # Flat pack-loop tuples: the two flat pack files resolve
        # tuple names against package ``core`` itself, so a packaged
        # stem literal there would fail at registration time.
        if rel in FLAT_PACK_FILES:
            for node in ast.walk(parsed):
                if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                    try:
                        vals = [ast.literal_eval(e) for e in node.iter.elts]
                    except Exception:
                        continue
                    if vals and all(isinstance(v, str) for v in vals) \
                            and "__import__" in ast.dump(node):
                        for v in vals:
                            if v in stems:
                                pack_tuples.append((rel, v))
    result = {
        "static_tools": static_tools,
        "flat_imports": flat_imports,
        "constructed": constructed,
        "patch_targets": patch_targets,
        "pack_tuples": pack_tuples,
    }
    _SCAN_CACHE[key] = result
    return result


def _scan_root_runners(code_lab: Path):
    """Return (core.tools imports, files with flat packaged imports).

    code_lab is the code-lab root; only the top-level runner files
    (test_v*.py, check_*.py) are scanned, never backup dirs.
    """
    # The stems come from the shipped tree, not the harness dir.
    stems = _packaged_stems(TREE)
    tools_hits = []
    flat_files = set()
    runners = sorted(code_lab.glob("test_v*.py")) + sorted(code_lab.glob("check_*.py"))
    for path in runners:
        try:
            parsed = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(parsed):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.replace("voice_assistant.", "").split(".")
                    if parts == ["core", "tools"]:
                        tools_hits.append((path.name, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                module = (node.module or "").replace("voice_assistant.", "")
                for alias in node.names:
                    if module == "core.tools":
                        tools_hits.append((path.name, node.lineno, alias.name))
                    elif module == "core" and alias.name == "tools":
                        tools_hits.append((path.name, node.lineno, alias.name))
                    elif module == "core" and alias.name in stems:
                        flat_files.add(path.name)
    return tools_hits, flat_files


def _import_time_nodes(body):
    """Import-time statements: body plus Try/If wrappers, recursively.

    Try/If wrappers still execute at import time; function and class
    bodies do not (their imports are deferred by definition), so the
    walk never descends into FunctionDef/ClassDef nodes.
    """
    for node in body:
        yield node
        if isinstance(node, ast.Try):
            for sub in node.body + node.orelse:
                yield from _import_time_nodes([sub])
            for handler in node.handlers:
                for sub in handler.body:
                    yield from _import_time_nodes([sub])
        elif isinstance(node, ast.If):
            for sub in node.body + node.orelse:
                yield from _import_time_nodes([sub])


def _import_time_edges(tree: Path):
    """(src_pkg, dst_pkg, dst_module) for import-time core imports.

    Resolves absolute and relative forms; src_pkg is the Phase 3
    package, 'core-flat', or another top-level dir.
    """
    edges = []
    core_dir = tree / "core"
    for dirpath, dirnames, filenames in os.walk(core_dir):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(tree)
            parts = list(rel.parts[:-1])
            if len(parts) > 1:
                src_pkg = parts[1]
            else:
                src_pkg = "core-flat"
            try:
                parsed = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in _import_time_nodes(parsed.body):
                if isinstance(node, ast.ImportFrom):
                    base = _resolve_from(tree, path, node.level, node.module)
                    if len(base) >= 2 and base[0] == "core" \
                            and base[1] in PHASE3_PACKAGES:
                        edges.append((src_pkg, base[1], ".".join(base),
                                      str(rel), node.lineno))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        mp = alias.name.replace("voice_assistant.", "").split(".")
                        if len(mp) >= 2 and mp[0] == "core" \
                                and mp[1] in PHASE3_PACKAGES:
                            edges.append((src_pkg, mp[1], ".".join(mp[:2]),
                                          str(rel), node.lineno))
    return edges


def _funnel_import_time_graph(tree: Path):
    """Intra-funnel import-time graph: module stem -> {stem}."""
    funnel_dir = tree / "core" / "funnel"
    members = {name[:-3] for name in os.listdir(funnel_dir)
               if name.endswith(".py") and name != "__init__.py"}
    graph = {}
    for name in sorted(members):
        path = funnel_dir / (name + ".py")
        try:
            parsed = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            graph[name] = set()
            continue
        deps = set()
        for node in _import_time_nodes(parsed.body):
            if isinstance(node, ast.ImportFrom):
                if node.level == 1 and node.module:
                    first = node.module.split(".")[0]
                    if first in members:
                        deps.add(first)
                elif node.level == 1 and not node.module:
                    for alias in node.names:
                        if alias.name in members:
                            deps.add(alias.name)
                elif node.level == 0 and node.module:
                    mp = node.module.replace("voice_assistant.", "").split(".")
                    if len(mp) == 3 and mp[0] == "core" and mp[1] == "funnel" \
                            and mp[2] in members:
                        deps.add(mp[2])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mp = alias.name.replace("voice_assistant.", "").split(".")
                    if len(mp) == 3 and mp[0] == "core" and mp[1] == "funnel" \
                            and mp[2] in members:
                        deps.add(mp[2])
        graph[name] = deps
    return graph


def _find_cycles(graph):
    """Return the list of import-time cycles (each a sorted tuple)."""
    cycles = []
    for start in graph:
        # Nodes mutually reachable with start (SCC via reachability;
        # the funnel graph is small, so this is fast enough).
        def _reach(src):
            seen, stack = set(), [src]
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(graph.get(cur, ()))
            return seen
        comp = _reach(start) & {m for m in graph if start in _reach(m)}
        if len(comp) > 1:
            key = tuple(sorted(comp))
            if key not in cycles:
                cycles.append(key)
        elif start in graph.get(start, ()):
            key = (start,)
            if key not in cycles:
                cycles.append(key)
    return cycles


class DeletionGuardTests(unittest.TestCase):
    """Phase 4 deletions stay deleted (D1/D2/S1c)."""

    def test_deleted_files_absent(self):
        present = [(rel, str(TREE / rel)) for rel, _ in DELETED_MODULES
                   if (TREE / rel).exists()]
        self.assertEqual(present, [], f"deleted module reappeared: {present}")

    def test_deleted_modules_not_importable(self):
        """Importability via a hermetic subprocess per module.

        A temp dir holds a symlink named voice_assistant pointing at
        the tree; the subprocess imports the deleted dotted name
        with ONLY that dir on PYTHONPATH, so no test-process
        sys.modules state can mask or fake the result. The import
        must fail with ModuleNotFoundError. PYTHONDONTWRITEBYTECODE
        keeps the probe from writing __pycache__ through the symlink
        into the real tree.
        """
        try:
            tmp = tempfile.TemporaryDirectory()
        except OSError as exc:
            self.skipTest(f"temp dir unavailable: {exc}")
        with tmp:
            link = Path(tmp.name) / "voice_assistant"
            try:
                os.symlink(str(TREE), str(link))
            except OSError as exc:
                self.skipTest(f"symlink unavailable: {exc}")
            env = dict(os.environ)
            env["PYTHONPATH"] = tmp.name
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            failures = []
            for _rel, dotted in DELETED_MODULES:
                proc = subprocess.run(
                    [sys.executable, "-c", f"import {dotted}"],
                    cwd=tmp.name, env=env, capture_output=True, text=True,
                    timeout=120)
                if proc.returncode == 0:
                    failures.append((dotted, "imported OK"))
                elif "ModuleNotFoundError" not in proc.stderr:
                    failures.append((dotted, proc.stderr.strip()[-200:]))
            self.assertEqual(failures, [],
                             f"deleted module still importable: {failures}")


class PackageBoundaryTests(unittest.TestCase):
    """Corrected hub-import rule + no core.tools anywhere (Phase 3/4)."""

    def test_no_core_tools_imports_in_tree(self):
        found = _scan_boundary(TREE)["static_tools"]
        self.assertEqual(found, [], f"core.tools imports in tree: {found[:10]}")

    def test_no_flat_imports_of_packaged_modules_in_tree(self):
        found = [hit for hit in _scan_boundary(TREE)["flat_imports"]
                 if (hit[0], hit[2]) not in ALLOWED_FLAT_IMPORTS]
        self.assertEqual(found, [], f"flat imports in tree: {found[:10]}")

    def test_no_old_path_constructed_imports_in_tree(self):
        found = _scan_boundary(TREE)["constructed"]
        self.assertEqual(found, [], f"constructed old-path imports: {found[:10]}")

    def test_no_old_path_patch_targets_in_tree(self):
        found = _scan_boundary(TREE)["patch_targets"]
        self.assertEqual(found, [], f"old-path patch targets: {found[:10]}")

    def test_flat_pack_tuples_name_no_packaged_stem(self):
        found = _scan_boundary(TREE)["pack_tuples"]
        self.assertEqual(found, [],
                         f"flat pack tuple names a packaged stem: {found[:10]}")

    def test_root_runners_locked(self):
        """Lab-layout only: core.tools zero + no NEW flat-import file.

        Skipped when the code-lab layout is absent (shipped
        installs have no root runners); mirrors the skip pattern
        in test_packaging's checker test.
        """
        code_lab = TREE.parent.parent.parent
        runners = sorted(code_lab.glob("test_v*.py")) + sorted(code_lab.glob("check_*.py"))
        if not runners:
            self.skipTest("code-lab root runners not present in this layout")
        tools_hits, flat_files = _scan_root_runners(code_lab)
        self.assertEqual(tools_hits, [],
                         f"core.tools imports in root runners: {tools_hits[:10]}")
        new_files = flat_files - KNOWN_STALE_ROOT_FILES
        self.assertEqual(new_files, set(),
                         f"new root-runner files with flat imports: {sorted(new_files)}")


class SameStemCollisionTests(unittest.TestCase):
    """M1a lesson: no flat module shares a stem with a package."""

    def test_no_flat_package_twin(self):
        core_dir = TREE / "core"
        flat_stems = {name[:-3] for name in os.listdir(core_dir)
                      if name.endswith(".py")}
        pkg_dirs = {name for name in os.listdir(core_dir)
                    if (core_dir / name).is_dir()
                    and (core_dir / name / "__init__.py").exists()}
        twins = flat_stems & pkg_dirs
        self.assertEqual(twins, set(),
                         f"flat module shares a stem with a package: {sorted(twins)}")

    def test_phase3_packages_exist(self):
        missing = [pkg for pkg in PHASE3_PACKAGES
                   if not (TREE / "core" / pkg).is_dir()]
        self.assertEqual(missing, [],
                         f"Phase 3 package dir missing: {missing}")


class PackageStructureTests(unittest.TestCase):
    """Phase 3 package rules: init shape, members, edge directions."""

    def test_package_inits_are_docstring_only(self):
        offenders = []
        for pkg in PHASE3_PACKAGES:
            init = TREE / "core" / pkg / "__init__.py"
            if not init.exists():
                offenders.append((pkg, "missing __init__.py"))
                continue
            try:
                parsed = ast.parse(init.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                offenders.append((pkg, f"unparseable: {exc}"))
                continue
            non_doc = [stmt for stmt in parsed.body
                       if not (isinstance(stmt, ast.Expr)
                               and isinstance(stmt.value, ast.Constant)
                               and isinstance(stmt.value.value, str))]
            if pkg in ALLOWED_INIT_REEXPORTS:
                # Allowlisted re-export shape ONLY: one relative
                # from-import of the named member plus __all__ naming
                # exactly that member. Anything else still offends.
                mod, name = ALLOWED_INIT_REEXPORTS[pkg]
                shape_ok = (
                    len(non_doc) == 2
                    and isinstance(non_doc[0], ast.ImportFrom)
                    and non_doc[0].level == 1
                    and non_doc[0].module == mod
                    and [a.name for a in non_doc[0].names] == [name]
                    and isinstance(non_doc[1], ast.Assign)
                    and len(non_doc[1].targets) == 1
                    and isinstance(non_doc[1].targets[0], ast.Name)
                    and non_doc[1].targets[0].id == "__all__"
                    and isinstance(non_doc[1].value, ast.List)
                    and [e.value for e in non_doc[1].value.elts
                         if isinstance(e, ast.Constant)] == [name]
                )
                if not shape_ok:
                    offenders.append((pkg, "non-docstring stmt outside "
                                     "allowlisted re-export shape: "
                                     f"{[s.lineno for s in non_doc]}"))
                continue
            for stmt in non_doc:
                offenders.append((pkg, f"non-docstring stmt line {stmt.lineno}"))
        self.assertEqual(offenders, [],
                         f"package __init__ not docstring-only: {offenders}")

    def test_all_package_members_parse(self):
        failures = []
        for pkg in PHASE3_PACKAGES:
            pkgdir = TREE / "core" / pkg
            members = [name for name in os.listdir(pkgdir)
                       if name.endswith(".py")]
            self.assertGreater(len(members), 1,
                               f"package {pkg} has no members")
            for name in members:
                try:
                    ast.parse((pkgdir / name).read_text(encoding="utf-8"))
                except SyntaxError as exc:
                    failures.append((f"core/{pkg}/{name}", str(exc)))
        self.assertEqual(failures, [], f"unparseable members: {failures}")

    def test_flat_pack_files_stay_flat(self):
        missing = [rel for rel in FLAT_PACK_FILES
                   if not (TREE / rel).exists()]
        moved = [f"core/funnel/{Path(rel).name}" for rel in FLAT_PACK_FILES
                 if (TREE / "core" / "funnel" / Path(rel).name).exists()]
        self.assertEqual(missing, [],
                         f"flat pack file missing from core/: {missing}")
        self.assertEqual(moved, [],
                         f"flat pack file duplicated under core/funnel/: {moved}")

    def test_desktop_to_funnel_import_time_zero(self):
        edges = [(f, mod) for (src, dst, mod, f, _ln)
                 in _import_time_edges(TREE)
                 if src == "desktop" and dst == "funnel"]
        self.assertEqual(edges, [],
                         f"desktop -> funnel import-time edges: {edges[:10]}")

    def test_brain_to_funnel_import_time_only_tool_result(self):
        edges = [(f, mod) for (src, dst, mod, f, _ln)
                 in _import_time_edges(TREE)
                 if src == "brain" and dst == "funnel"]
        bad = [(f, mod) for (f, mod) in edges
               if mod != "core.funnel.tool_result"]
        self.assertEqual(bad, [],
                         f"brain -> funnel import-time edges beyond tool_result: {bad[:10]}")

    def test_tool_result_import_time_stdlib_only(self):
        """The cycle-free justification for the planner edge above:
        tool_result must import only the standard library at import
        time, so planner -> tool_result cannot close a cycle back
        into brain (or anywhere else in the product tree)."""
        path = TREE / "core" / "funnel" / "tool_result.py"
        parsed = ast.parse(path.read_text(encoding="utf-8"))
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        offenders = []
        for node in _import_time_nodes(parsed.body):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] not in stdlib:
                        offenders.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level != 0:
                    offenders.append(f"relative import level {node.level}")
                elif node.module and node.module.split(".")[0] not in stdlib:
                    offenders.append(node.module)
        self.assertEqual(offenders, [],
                         f"tool_result imports non-stdlib at import time: {offenders}")

    def test_funnel_import_time_acyclic(self):
        cycles = _find_cycles(_funnel_import_time_graph(TREE))
        self.assertEqual(cycles, [],
                         f"funnel import-time cycles: {cycles[:5]}")


class PackagingGapTests(unittest.TestCase):
    """check_packaging outcome locked: only the w8_wiring gap allowed."""

    def test_no_new_packaging_gap(self):
        code_lab = TREE.parent.parent.parent
        checker = code_lab / "check_packaging.py"
        if not checker.exists():
            self.skipTest("code-lab checker not present in this layout")
        sys.path.insert(0, str(code_lab))
        try:
            import check_packaging
        finally:
            sys.path.remove(str(code_lab))
        missing, fresh = check_packaging.check(
            tree_root=TREE, spec_path=TREE / "installer" / "vyuk.spec")
        extra = missing - ALLOWED_PACKAGING_GAP
        self.assertEqual(extra, set(),
                         f"new packaging gaps beyond w8_wiring: {sorted(extra)}")
        self.assertTrue(fresh, "catalogue snapshot is stale")


if __name__ == "__main__":
    unittest.main()
