"""VYUK shipped-test runner (v5.45, P-1).

Runs the unit tests in this folder with the standard library
only — no pytest, no GUI, no network:

    python tests/run_tests.py            (from anywhere)
    python -m tests.run_tests            (from the install root —
                                          equivalent discovery)

Exit code 0 means every test passed (skips for missing optional
dependencies are reported, not failures); anything else exits 1.

This folder is test-only: the app never imports it at runtime.

CANONICAL CI COMMAND (audit fix Batch 5, TEST-04 — the full
procedure lives in tests/CANONICAL_CI.md, which is the single
canonical document; this docstring is its pointer, not a
second copy):

    # 1. Restore data to baseline FIRST (delete-first, never
    #    overlay; any run containing timeout tests starts
    #    from restored data — the standing C10 rule):
    #    rm -rf data/* && cp -r <data-backup>/. data/
    # 2. Run under the Qt venv with the syslibs EGL env:
    #    LD_LIBRARY_PATH=$HOME/workspace/code-lab/.syslibs/root/usr/lib/x86_64-linux-gnu \
    #    __EGL_VENDOR_LIBRARY_DIRS=$HOME/workspace/code-lab/.syslibs/root/usr/share/glvnd/egl_vendor.d \
    #    QT_QPA_PLATFORM=offscreen PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 \
    #    <venv-qt>/bin/python tests/run_tests.py
    # 3. Restore data again afterwards and classify the run
    #    with tests/classify_run.py (report-only, never a
    #    gate). Judged BY CLASSIFICATION, not count: see
    #    CANONICAL_CI.md for the bucket rules.

CHUNKED RUNS (GAPS-30, T-V7): the full suite does NOT run in
one process — it OOM-kills there (327 modules at review time,
389 now). The full-suite path is one module per subprocess:

    python tests/run_tests.py --modules            (all modules)
    python tests/run_tests.py --modules test_a test_b
    python tests/run_tests.py --modules chunk_00   (a file with
        one test_-prefixed module name per line)

Each module runs under the same data guard as the single-
process path, in the same interpreter and environment as the
parent. Exit 0 iff every module exits 0.

COVERAGE (GAPS-30, T-V2): stdlib trace only, no coverage.py:

    python tests/run_tests.py --coverage [--modules ...]
        [--coverage-out tests/coverage_report.json]

Runs the modules chunked (as above), each under
trace.Trace(count=1), and merges the per-module counts into a
line-coverage report over core/ + gui/ + main.py + doctor.py.
The report is the measurement; the floor it is judged against
lives in tests/coverage_floor.json (measured percent + floor
+ method + scope — see that file). --coverage never gates on
the floor itself: it reports the percent, and the floor file
is the human record of what the measured baseline was.

    python tests/run_tests.py --coverage \
        --coverage-parts <persistent-dir>

keeps each module's trace counts in <persistent-dir> so a
killed run resumes from the parts already there (counts are
per-module and additive, so a resumed merge equals a single
uninterrupted run's merge). Parts are valid only while
core/ + gui/ are unchanged: after a product-code change,
delete the parts dir and re-run from scratch.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import trace
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
_PACKAGE_ROOT = _HERE.parent

# Product files coverage is measured over (never the tests
# themselves, never the stdlib, never site-packages).
_PRODUCT_DIRS = ("core", "gui")
_PRODUCT_TOP = ("main.py", "doctor.py")


def _load_guard():
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "_activity_guard", str(_HERE / "_activity_guard.py"))
    _guard_mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_guard_mod)
    return _guard_mod


def _prepare_path():
    # `core` (and siblings) import as top-level packages from the
    # install root; the test modules import as top-level too.
    for p in (str(_PACKAGE_ROOT), str(_HERE)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _run_suite(module_name: str | None) -> int:
    """Run one module (or the whole discovery) under the data guard."""
    _prepare_path()
    loader = unittest.TestLoader()
    if module_name:
        suite = loader.loadTestsFromName(module_name)
    else:
        suite = loader.discover(
            start_dir=str(_HERE), pattern="test_*.py",
            top_level_dir=str(_HERE))
    runner = unittest.TextTestRunner(verbosity=1)
    # v5.88 gate: several suites execute tools through the real
    # registry, which appends to data/activity.log and data/audit.jsonl
    # — correct app behaviour that a test run must not leave behind
    # in the real data/ tree (the v5.45 p1 gate proves the runner
    # leaves activity.log byte-identical). Guard the whole run with
    # the shipped _activity_guard trio (Batch 5, TEST-01: the
    # receipt guard joins activity + audit, so the run also
    # leaves data/tool_receipts.jsonl byte-identical); suites
    # that guard themselves nest harmlessly (inner snapshot ==
    # guarded state). The whole-run guard is the AFTER-half
    # only — suites containing timeout tests still restore
    # data to baseline BEFORE they run (their own setUp).
    guard = _load_guard()
    with guard.ActivityLogGuard(_PACKAGE_ROOT), \
            guard.AuditLogGuard(_PACKAGE_ROOT), \
            guard.ReceiptLogGuard(_PACKAGE_ROOT):
        result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


def _run_one_traced(module_name: str, trace_out: str) -> int:
    """Run one module under trace.Trace(count=1) and write the
    per-module line counts (product files only) to trace_out."""
    _prepare_path()
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName(module_name)
    runner = unittest.TextTestRunner(verbosity=1)
    guard = _load_guard()
    tracer = trace.Trace(count=1, trace=0, ignoredirs=[
        sys.prefix, sys.base_prefix, str(_HERE)])
    with guard.ActivityLogGuard(_PACKAGE_ROOT), \
            guard.AuditLogGuard(_PACKAGE_ROOT), \
            guard.ReceiptLogGuard(_PACKAGE_ROOT):
        result = tracer.runfunc(runner.run, suite)
    counts = tracer.results().counts
    root = _PACKAGE_ROOT.resolve()
    product: dict[str, dict[str, int]] = {}
    # trace.CoverageResults.counts maps (filename, lineno)
    # tuples to hit counts.
    for (filename, lineno), count in counts.items():
        try:
            rel = pathlib.Path(filename).resolve().relative_to(root)
        except ValueError:
            continue
        if rel.parts[0] in _PRODUCT_DIRS or rel.name in _PRODUCT_TOP:
            product.setdefault(str(rel), {})[str(lineno)] = int(count)
    pathlib.Path(trace_out).write_text(
        json.dumps(product), encoding="utf-8")
    return 0 if result.wasSuccessful() else 1


def _executable_lines(path: pathlib.Path) -> set[int]:
    """Lines with bytecode in a file, via co_lines() — the
    denominator of line coverage, stdlib only."""
    try:
        code = compile(path.read_text(encoding="utf-8"),
                       str(path), "exec")
    except (SyntaxError, UnicodeDecodeError, OSError):
        return set()
    lines: set[int] = set()

    def walk(c):
        for _start, _end, line in c.co_lines():
            if line is not None:
                lines.add(line)
        for const in c.co_consts:
            if hasattr(const, "co_lines"):
                walk(const)

    walk(code)
    return lines


def _product_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for dirname in _PRODUCT_DIRS:
        files.extend(sorted((_PACKAGE_ROOT / dirname).rglob("*.py")))
    for name in _PRODUCT_TOP:
        path = _PACKAGE_ROOT / name
        if path.is_file():
            files.append(path)
    return [f for f in files if "__pycache__" not in f.parts]


def _coverage_report(merged: dict, modules: list[str]) -> dict:
    """Merge per-module counts into a per-file + total report."""
    files: dict[str, dict] = {}
    total_exec = total_cov = 0
    for path in _product_files():
        rel = str(path.relative_to(_PACKAGE_ROOT))
        executable = _executable_lines(path)
        counted = merged.get(rel, {})
        covered = {int(ln) for ln in counted} & executable
        files[rel] = {"executable": len(executable),
                      "covered": len(covered),
                      "percent": (round(100.0 * len(covered)
                                        / len(executable), 2)
                                  if executable else 100.0)}
        total_exec += len(executable)
        total_cov += len(covered)
    total = {"executable": total_exec, "covered": total_cov,
             "percent": (round(100.0 * total_cov / total_exec, 2)
                         if total_exec else 100.0)}
    return {"modules_run": modules, "files": files, "total": total}


def _all_modules() -> list[str]:
    return sorted(p.stem for p in _HERE.glob("test_*.py"))


def _normalize_modules(raw: list[str]) -> list[str]:
    """Space-separated names, a chunk file, or both — all to bare
    test_-prefixed stems (loadTestsFromName form)."""
    out: list[str] = []
    for item in raw:
        path = pathlib.Path(item)
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
            continue
        out.append(item)
    normalized: list[str] = []
    for name in out:
        name = name.strip()
        if name.startswith("tests."):
            name = name[len("tests."):]
        if name.startswith("tests/"):
            name = name[len("tests/"):]
        if name.endswith(".py"):
            name = name[:-3]
        normalized.append(name)
    # Dedup, keep order.
    seen: set[str] = set()
    deduped = [n for n in normalized if not (n in seen or seen.add(n))]
    return deduped


def _run_chunked(modules: list[str], traced: bool,
                 trace_dir: str | None,
                 parts_dir: str | None = None) -> tuple[int, dict]:
    """One subprocess per module, sequential. Returns
    (exit code, merged trace counts).

    With parts_dir (coverage only): each module's trace counts
    are written to parts_dir/<module>.json and KEPT there, so a
    killed run resumes — modules whose part file already exists
    are skipped and their counts merged from the file. Counts
    are per-module and additive, so a resumed merge equals a
    single uninterrupted run's merge. Parts are valid only
    while core/ + gui/ are unchanged: after a product-code
    change, delete the parts dir and re-run from scratch."""
    failed: list[str] = []
    merged: dict[str, dict[str, int]] = {}
    skipped = 0
    for mod in modules:
        part = (os.path.join(parts_dir, mod + ".json")
                if parts_dir else None)
        if traced and part and os.path.isfile(part):
            print(f"[{mod}] skipped (part present)", flush=True)
            skipped += 1
            trace_out = part
        else:
            cmd = [sys.executable, str(_HERE / "run_tests.py"),
                   "--run-one-traced" if traced else "--run-one",
                   mod]
            trace_out = None
            if traced:
                trace_out = part or os.path.join(trace_dir,
                                                 mod + ".json")
                cmd += ["--trace-out", trace_out]
            print(f"[{mod}] ...", flush=True)
            proc = subprocess.run(cmd)
            print(f"[{mod}] exit={proc.returncode}", flush=True)
            if proc.returncode != 0:
                failed.append(mod)
        if traced and trace_out and os.path.isfile(trace_out):
            data = json.loads(pathlib.Path(trace_out).read_text(
                encoding="utf-8"))
            for rel, lines in data.items():
                merged.setdefault(rel, {})
                for ln, count in lines.items():
                    merged[rel][ln] = merged[rel].get(ln, 0) + int(count)
        elif traced:
            failed.append(mod + " (no trace output)")
    if skipped:
        print(f"coverage parts: {skipped} skipped, "
              f"{len(modules) - skipped} ran", flush=True)
    return (0 if not failed else 1), merged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="VYUK shipped-test runner")
    parser.add_argument("--modules", nargs="*", default=None,
                        help="run each named module in its own "
                        "subprocess (names, or a file with one "
                        "name per line); empty = all modules")
    parser.add_argument("--coverage", action="store_true",
                        help="trace-based line coverage over "
                        "core/ + gui/ (chunked, like --modules)")
    parser.add_argument("--coverage-out", default=None,
                        help="where to write the coverage report "
                        "JSON (default tests/coverage_report.json)")
    parser.add_argument("--coverage-parts", default=None,
                        help="persistent dir for per-module trace "
                        "parts: a killed coverage run resumes from "
                        "the parts already there (see _run_chunked); "
                        "delete the dir and re-run after a "
                        "product-code change")
    parser.add_argument("--run-one", default=None,
                        help="internal: run one module (subprocess "
                        "entry point for --modules)")
    parser.add_argument("--run-one-traced", default=None,
                        help="internal: run one module under trace "
                        "(subprocess entry point for --coverage)")
    parser.add_argument("--trace-out", default=None,
                        help="internal: trace counts output file")
    args = parser.parse_args(argv)

    if args.run_one_traced:
        if not args.trace_out:
            print("--run-one-traced needs --trace-out")
            return 2
        return _run_one_traced(args.run_one_traced, args.trace_out)
    if args.run_one:
        return _run_suite(args.run_one)

    if args.coverage or args.modules is not None:
        modules = (_normalize_modules(args.modules)
                   if args.modules else _all_modules())
        if not modules:
            print("no modules to run")
            return 2
        if args.coverage:
            out_path = (pathlib.Path(args.coverage_out)
                        if args.coverage_out
                        else _HERE / "coverage_report.json")
            if args.coverage_parts:
                parts_dir = args.coverage_parts
                os.makedirs(parts_dir, exist_ok=True)
                code, merged = _run_chunked(modules, True, None,
                                            parts_dir)
            else:
                tmpdir = tempfile.mkdtemp(prefix="vyuk_coverage_")
                try:
                    code, merged = _run_chunked(modules, True, tmpdir)
                finally:
                    shutil.rmtree(tmpdir, ignore_errors=True)
            report = _coverage_report(merged, modules)
            out_path.write_text(json.dumps(report, indent=2),
                                encoding="utf-8")
            total = report["total"]
            print(f"coverage: {total['covered']}/"
                  f"{total['executable']} lines "
                  f"({total['percent']}%) over "
                  f"{len(report['files'])} product files, "
                  f"{len(modules)} modules")
            print(f"report: {out_path}")
            return code
        code, _ = _run_chunked(modules, False, None)
        return code

    return _run_suite(None)


if __name__ == "__main__":
    sys.exit(main())
