"""Field-check fix #33 (round-2 item 13): find_files is slow on a rare
home pattern — a full-home walk took 75–88 s in the round-2 field
check (N1 follow-up: "fieldcheck_probe*" over home, 88,506 ms).

The fix, all in core/skills_plus_apps_files.py:
- the walk stops at a 10 s time budget (_FIND_TIME_BUDGET_S) and says
  so honestly in the scope line, instead of making a voice turn wait
  for the whole disk;
- tool-managed trees are skipped by policy (_is_policy_skip_dir plus
  the venv/conda marker rule): environments and package trees are
  not user files, the same principle as the ".git"/"node_modules"
  skips that predate this fix;
- folders are visited in a fixed order (visible before hidden,
  alphabetical within each group), so the same question returns the
  same first files instead of filesystem order.

What this suite proves, deterministically (no timing assertion tighter
than a 10 s generous bound on a small fixture):
- env/package trees are skipped while user files are still found;
- on a fixture without env-shaped dirs the results are identical to
  a reference walk using the old policy;
- the budget stops the walk mid-flight with the honest note (fake
  clock, so the cutoff is exact, not timing-dependent);
- an explicit root that IS an environment is still searchable (the
  marker rule exempts the base itself).
"""
from __future__ import annotations

import fnmatch
import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.skills_plus_apps_files as apps_files  # noqa: E402
from core.skills_plus_apps_files import (  # noqa: E402
    _FIND_EXPLICIT_MAX_DEPTH,
    _FIND_SKIP_DIRS,
    find_files,
)


def _write(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _listed_paths(out):
    """The file paths find_files lists, one per bullet line."""
    found = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("• "):
            found.append(line[2:].rsplit(" (", 1)[0])
    return found


class _FakeClock:
    """monotonic() returns 0.0 for the first `live_calls` calls, then
    a huge value — the walk runs that many clock reads, then the
    budget fires. The module reads the clock once to arm the
    deadline, once per directory, and once per 256 files."""

    def __init__(self, live_calls):
        self._remaining = live_calls

    def monotonic(self):
        if self._remaining > 0:
            self._remaining -= 1
            return 0.0
        return 1000.0


class FindFilesSpeedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="findfiles_speed_")
        self._root = pathlib.Path(self._tmp) / "home"
        self._root.mkdir()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- pruning ---------------------------------------------------
    def test_env_and_package_trees_skipped_user_files_found(self):
        probe = self._root / "probe_target.txt"
        _write(probe)
        _write(self._root / "docs" / "probe_notes.txt")
        # A virtualenv by marker: files inside it are not found.
        venv = self._root / ".venv-foo"
        _write(venv / "pyvenv.cfg", "home = /usr\n")
        _write(venv / "bin" / "probe_venv.txt")
        _write(venv / "lib" / "probe_lib.txt")
        # A virtualenv by name shape alone (no marker).
        _write(self._root / "mic_field_venv" / "probe_venv2.txt")
        # A conda env by marker: its subfolders are not descended
        # into, but its own top-level files are still matched.
        conda = self._root / "myconda"
        _write(conda / "conda-meta" / "probe_conda.txt")
        _write(conda / "probe_conda_top.txt")
        # An installed package tree by name.
        _write(self._root / "proj" / "site-packages" / "probe_pkg.txt")
        _write(self._root / "proj" / "probe_proj.txt")
        # Pre-existing policy still holds.
        _write(self._root / "proj" / "node_modules" / "probe_nm.txt")
        out = find_files("probe*", root=str(self._root))
        listed = _listed_paths(out)
        self.assertIn(str(probe), listed)
        self.assertIn(str(self._root / "docs" / "probe_notes.txt"), listed)
        self.assertIn(str(self._root / "proj" / "probe_proj.txt"), listed)
        for name in ("probe_venv.txt", "probe_lib.txt", "probe_venv2.txt",
                     "probe_conda.txt",
                     "probe_pkg.txt", "probe_nm.txt"):
            self.assertNotIn(name, out)
        # ...except the env root's own top-level file, which is matched.
        self.assertIn("probe_conda_top.txt", out)
        self.assertIn("skipped by policy", out)

    def test_explicit_env_root_still_searchable(self):
        # The marker rule exempts the base itself: a caller who
        # explicitly names an environment as the root still searches it.
        venv = self._root / ".venv-foo"
        _write(venv / "pyvenv.cfg", "home = /usr\n")
        _write(venv / "probe_inside.txt")
        out = find_files("probe_inside", root=str(venv))
        self.assertIn("probe_inside.txt", out)

    # -- results identical where the policy is unchanged ------------
    def test_results_identical_to_reference_walk(self):
        fixture = self._root / "tree"
        names = []
        for rel in ("a.txt", "b/c.txt", "b/d.log", ".hidden/e.txt",
                    "docs/report.txt", "docs/old/f.txt", "x/y/z.txt"):
            p = fixture / rel
            _write(p)
            names.append(str(p))
        # Reference: the old walk — exact _FIND_SKIP_DIRS only, no
        # ordering, no budget, depth cap for an explicit root.
        ref = []
        for dirpath, dirnames, filenames in os.walk(fixture):
            depth = (len(pathlib.Path(dirpath).relative_to(fixture).parts)
                     if pathlib.Path(dirpath) != fixture else 0)
            dirnames[:] = [d for d in dirnames if d not in _FIND_SKIP_DIRS]
            if depth >= _FIND_EXPLICIT_MAX_DEPTH:
                dirnames[:] = []
            for fn in filenames:
                if fnmatch.fnmatchcase(fn.lower(), "*txt*"):
                    ref.append(str(pathlib.Path(dirpath) / fn))
        out = find_files("*txt*", root=str(fixture), limit=50)
        self.assertEqual(sorted(ref), sorted(_listed_paths(out)))

    # -- the budget -------------------------------------------------
    def test_budget_stops_walk_with_honest_note(self):
        # Root (probe_shallow) -> a -> b -> c ... : the fake clock
        # lets the walk visit the root, a and b, then fires on c.
        _write(self._root / "probe_shallow.txt")
        _write(self._root / "a" / "probe_a.txt")
        _write(self._root / "b" / "probe_b.txt")
        deep = self._root / "c"
        for i in range(5):
            _write(deep / f"level{i}" / "probe_deep.txt")
        real_time = apps_files.time
        apps_files.time = _FakeClock(live_calls=4)
        try:
            out = find_files("probe*", root=str(self._root))
        finally:
            apps_files.time = real_time
        self.assertIn("probe_shallow.txt", out)
        self.assertIn("probe_a.txt", out)
        self.assertIn("probe_b.txt", out)
        self.assertNotIn("probe_deep.txt", out)
        self.assertIn("time budget", out)
        self.assertIn("was not searched", out)
        self.assertIn("narrow the root", out)

    def test_budget_zero_returns_honest_note(self):
        _write(self._root / "probe_x.txt")
        real_budget = apps_files._FIND_TIME_BUDGET_S
        apps_files._FIND_TIME_BUDGET_S = 0.0
        try:
            start = time.monotonic()
            out = find_files("probe*", root=str(self._root))
            dt = time.monotonic() - start
        finally:
            apps_files._FIND_TIME_BUDGET_S = real_budget
        self.assertLess(dt, 5.0)
        self.assertIn("time budget", out)
        self.assertIn("was not searched", out)

    def test_extended_path_respects_budget(self):
        _write(self._root / "probe_new.txt")
        real_budget = apps_files._FIND_TIME_BUDGET_S
        apps_files._FIND_TIME_BUDGET_S = 0.0
        try:
            out = find_files("probe*", root=str(self._root),
                             newest_first=True)
        finally:
            apps_files._FIND_TIME_BUDGET_S = real_budget
        self.assertIn("time budget", out)

    def test_large_fixture_completes_with_generous_bound(self):
        # 3,000 files across 300 dirs: a rare pattern must answer
        # far inside the 10 s budget (it takes ~0.1 s here); the
        # bound is generous so the test is not timing-flaky.
        for d in range(300):
            for f in range(10):
                _write(self._root / f"d{d:03d}" / f"file{f}.txt")
        _write(self._root / "needle_xyz.txt")
        start = time.monotonic()
        out = find_files("needle_xyz", root=str(self._root))
        dt = time.monotonic() - start
        self.assertLess(dt, 10.0)
        self.assertIn("needle_xyz.txt", out)


if __name__ == "__main__":
    unittest.main()
