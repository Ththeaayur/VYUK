"""F-01 + F-02 regression suite (Final Systemic Audit, 2026-10-30).

F-01: the snippet store was an unlocked read-modify-write with a
FIXED temp name, so concurrent saves could lose an update and
interleave in one shared temp file. The fix is a lock spanning read
through commit plus mkstemp-unique temp files, extended to the other
JSON stores with the same pattern (scheduler, tool_operations,
planner, confirm_gate, memory_files) and verified already-safe for
task_ledger + playbook (mkstemp already).

F-02: skills_system_health tools ignored _save_json's False return and
reported success anyway (wifi start/add/stop, peripheral roll call,
screen-time counter). Every site now checks and reports honestly.

Lab honesty (read this before extending the file):
- DATA_DIR is monkeypatched to a temp dir per test and restored in
  tearDown (addCleanup); no test ever touches the shipped data/.
- confirm_gate's allowlist path is tree-anchored (not DATA_DIR
  patchable), so it gets an AST pin only, never a behavioral write.
- The two-process test asserts NO CORRUPTION + NO TEMP LITTER only.
  It deliberately does NOT assert both processes' updates persist:
  the lock is in-process only, so cross-process last-writer-wins
  remains a documented residual (see skills_productivity's store
  section). A test pinning both-persist across processes would fail
  by design and would be a lie to keep green.
- No test touches the funnel, so no budget snapshot/restore is
  needed (testiso guest rule: only funnel-involving suites need it).
"""

import ast
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import core.config as cfg
from core import skills_productivity as sp
# INTENTIONAL (Phase C batch 14): skills_system6 renamed to skills_system_health; alias kept, behaviour unchanged.
from core import skills_system_health as s6
# Phase A batch A24 INTENTIONAL: the system6 split moved the
# tools into impl modules; tests patch names where they are
# bound (the defining impl module), not the facade, because
# patching a facade binding alone never drives impl code.
# No fake value or assertion changes.
from core import skills_system6_diag as s6_diag
from core import skills_system6_devices as s6_devices
from core.brain.planner import _save_list as planner_save_list  # Phase 3 B2-remainder INTENTIONAL: planner moved to core/brain/
from core.scheduler import ProactiveScheduler
from core.funnel.tool_operations import _write_file as ops_write_file  # Phase 3 F2a INTENTIONAL: tool_operations moved to core/funnel/

TREE = Path(__file__).resolve().parent.parent


def _fn_source(module_path: str, func_name: str,
               class_name: str | None = None) -> str:
    """Source segment of one function (optionally inside a class)."""
    tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    nodes = tree.body
    if class_name is not None:
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                nodes = node.body
                break
        else:
            raise AssertionError(f"class {class_name} not found")
    for node in nodes:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(
                Path(module_path).read_text(encoding="utf-8"), node) or ""
    raise AssertionError(f"function {func_name} not found")


_FIXED_TMP_PATTERNS = (
    'with_name(path.name + ".tmp")',
    'with_suffix(".tmp")',
    'with_suffix(path.suffix + ".tmp")',
    'path + ".tmp"',
    'str(path) + ".tmp"',
)


def _assert_unique_tmp(testcase, segment: str, where: str) -> None:
    """A saver uses a unique temp name, never a fixed one."""
    testcase.assertTrue(
        "mkstemp" in segment or "uuid4" in segment,
        f"{where}: temp file is not unique (no mkstemp/uuid4)")
    for pat in _FIXED_TMP_PATTERNS:
        testcase.assertNotIn(pat, segment,
                             f"{where}: still uses fixed temp {pat!r}")


class _TempDataDir(unittest.TestCase):
    """Base: DATA_DIR patched to a temp dir, restored on cleanup."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory(prefix="f01f02_")
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self._old_data_dir = cfg.DATA_DIR
        cfg.DATA_DIR = self.dir
        self.addCleanup(setattr, cfg, "DATA_DIR", self._old_data_dir)

    def _patch(self, module, name, value):
        old = getattr(module, name)
        setattr(module, name, value)
        self.addCleanup(setattr, module, name, old)
        return old


class SnippetContentionTests(_TempDataDir):
    """F-01: the audit's reproduction, now passing."""

    def test_two_threads_both_updates_persist(self):
        barrier = threading.Barrier(2)

        def worker(prefix):
            for i in range(30):
                barrier.wait(timeout=30)
                sp.save_snippet(f"{prefix}_{i}", "x" * 12)

        threads = [threading.Thread(target=worker, args=(p,))
                   for p in ("alpha", "beta")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertFalse(any(t.is_alive() for t in threads),
                         "worker thread hung")
        store, _dropped, _corrupt = sp._load_store()
        for i in range(30):
            self.assertIn(f"alpha_{i}", store)
            self.assertIn(f"beta_{i}", store)

    def test_same_key_race_never_corrupts(self):
        barrier = threading.Barrier(2)

        def worker(text):
            for _ in range(30):
                barrier.wait(timeout=30)
                sp.save_snippet("race", text)

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in ("text-A", "text-B")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        store, _dropped, _corrupt = sp._load_store()
        self.assertIn(store["race"]["text"], ("text-A", "text-B"))
        # The file itself must be valid JSON, not a torn mix.
        json.loads((self.dir / "snippets.json").read_text(
            encoding="utf-8"))

    def test_delete_vs_save_race_stays_consistent(self):
        sp.save_snippet("victim", "bye")
        barrier = threading.Barrier(2)

        def deleter():
            barrier.wait(timeout=30)
            sp.delete_snippet("victim")

        def saver():
            for i in range(10):
                barrier.wait(timeout=30) if i == 0 else None
                sp.save_snippet(f"kept_{i}", "y" * 5)

        # One barrier round: delete and first save start together.
        t1 = threading.Thread(target=deleter)
        t2 = threading.Thread(target=saver)
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)
        store, _dropped, _corrupt = sp._load_store()
        self.assertNotIn("victim", store)
        for i in range(10):
            self.assertIn(f"kept_{i}", store)

    def test_raw_save_store_concurrent_writes_no_litter(self):
        """Direct _save_store calls (the cross-process write pattern,
        lock bypassed): the file stays valid and no temp litter
        remains, because every temp name is unique."""
        barrier = threading.Barrier(2)

        def worker(tag):
            for i in range(25):
                barrier.wait(timeout=30) if i == 0 else None
                sp._save_store({f"{tag}_{i}": {"text": "z",
                                               "saved_at": ""}})

        threads = [threading.Thread(target=worker, args=(t,))
                   for t in ("p", "q")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        data = json.loads((self.dir / "snippets.json").read_text(
            encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertEqual([p.name for p in self.dir.iterdir()],
                         ["snippets.json"])
        self.assertFalse((self.dir / "snippets.json.tmp").exists())

    def test_single_writer_honesty_preserved(self):
        self.assertIn("Saved snippet",
                      sp.save_snippet("ok", "hello"))
        self._patch(sp, "_save_store", lambda store: False)
        self.assertIn("Couldn't save snippet",
                      sp.save_snippet("bad", "hello"))

    def test_two_processes_no_corruption_no_litter(self):
        """Separate processes (no shared lock): asserts NO
        CORRUPTION + NO TEMP LITTER only. It deliberately does NOT
        assert both processes' updates persist — cross-process
        last-writer-wins is the documented residual (see module
        docstring); asserting both-persist here would fail by
        design."""
        script = (
            "import sys; from pathlib import Path; "
            "import core.config as cfg; "
            "cfg.DATA_DIR = Path(sys.argv[1]); "
            "from core import skills_productivity as sp; "
            "tag = sys.argv[2]; "
            "[sp.save_snippet(f'{tag}_{i}', 'w' * 8) "
            " for i in range(20)]"
        )
        env = dict(os.environ, PYTHONPATH=str(TREE))
        procs = [subprocess.Popen(
            [sys.executable, "-c", script, str(self.dir), tag],
            cwd=str(TREE), env=env) for tag in ("procA", "procB")]
        for p in procs:
            rc = p.wait(timeout=120)
            self.assertEqual(rc, 0, "worker process failed")
        data = json.loads((self.dir / "snippets.json").read_text(
            encoding="utf-8"))
        self.assertIsInstance(data, dict)
        self.assertTrue(data, "final store must hold at least one save")
        self.assertEqual([p.name for p in self.dir.iterdir()],
                         ["snippets.json"])


class WifiHonestyTests(_TempDataDir):
    """F-02: wifi_dead_spot_map, all three branches."""

    def test_add_reading_failure_has_no_saved_string(self):
        self._patch(s6_diag, "_signal_pct", lambda: 67)
        self._patch(s6_diag, "_save_json", lambda p, d: False)
        result = s6.wifi_dead_spot_map("add_reading", "Kitchen")
        self.assertNotIn("Saved:", result)
        self.assertIn("wasn't recorded", result)
        self.assertIn("not kept", result)

    def test_start_failure_does_not_claim_started(self):
        self._patch(s6_diag, "_save_json", lambda p, d: False)
        result = s6.wifi_dead_spot_map("start")
        self.assertNotIn("Wi-Fi dead-spot walk started.", result)
        self.assertIn("couldn't start", result)

    def test_stop_failure_does_not_claim_kept(self):
        state = {"session_started": 12345.0, "readings": [
            {"room": "Kitchen", "signal": 67, "when": 12345.0}]}
        (self.dir / "wifi_dead_spots.json").write_text(
            json.dumps(state), encoding="utf-8")
        self._patch(s6_diag, "_save_json", lambda p, d: False)
        result = s6.wifi_dead_spot_map("stop")
        self.assertNotIn("Walk stopped", result)
        self.assertIn("couldn't stop", result)

    def test_success_paths_unchanged(self):
        self._patch(s6_diag, "_signal_pct", lambda: 67)
        self.assertIn("walk started",
                      s6.wifi_dead_spot_map("start"))
        result = s6.wifi_dead_spot_map("add_reading", "Kitchen")
        self.assertIn("Saved: Kitchen", result)
        self.assertIn("67%", result)
        saved = json.loads((self.dir / "wifi_dead_spots.json")
                           .read_text(encoding="utf-8"))
        self.assertEqual(len(saved["readings"]), 1)
        self.assertIn("Walk stopped",
                      s6.wifi_dead_spot_map("stop"))


class PeripheralAndScreenTimeTests(_TempDataDir):
    """F-02: the other bare _save_json sites in the same module."""

    _DEVICES = [{"name": "USB Mouse", "kind": "USB"},
                {"name": "BT Headset", "kind": "Bluetooth"}]

    def test_peripheral_failure_no_saved_claim(self):
        self._patch(s6_devices, "_is_windows", lambda: True)
        self._patch(s6_devices, "_list_devices", lambda: list(self._DEVICES))
        self._patch(s6_devices, "_save_json", lambda p, d: False)
        result = s6.peripheral_roll_call()
        self.assertNotIn("I've saved", result)
        self.assertIn("couldn't save", result)

    def test_peripheral_success_claims_saved(self):
        self._patch(s6_devices, "_is_windows", lambda: True)
        self._patch(s6_devices, "_list_devices", lambda: list(self._DEVICES))
        result = s6.peripheral_roll_call()
        self.assertIn("I've saved", result)
        self.assertTrue(s6._peripheral_path().exists())

    def test_screen_time_failure_note_present(self):
        self._patch(s6_devices, "_save_json", lambda p, d: False)
        result = s6.screen_time_today()
        self.assertIn("was not saved", result)

    def test_screen_time_success_no_note(self):
        result = s6.screen_time_today()
        self.assertNotIn("was not saved", result)

    def test_screen_time_final_branch_failure_note(self):
        now = s6._now()
        seed = {"date": s6._today_str(), "started_at": now - 3600,
                "active_seconds": 600.0, "last_check": now - 60,
                "checks": 5, "previous_day": None}
        s6._screen_time_path().write_text(json.dumps(seed),
                                          encoding="utf-8")
        self._patch(s6_devices, "_idle_seconds", lambda: 0)
        self._patch(s6_devices, "_save_json", lambda p, d: False)
        result = s6.screen_time_today()
        self.assertIn("Active use today:", result)
        self.assertIn("was not saved", result)


class StoreAuditTests(unittest.TestCase):
    """Per-store dispositions, pinned in code (F-01 audit table).

    Fixed here: snippets, scheduler, tool_operations, planner,
    confirm_gate, memory_files. Already safe (mkstemp before this
    batch): task_ledger, playbook. kill_chain is deliberately NOT
    pinned as fixed: its edit was reverted (not load-bearing for
    F-01/F-02; transitions are already _lock-serialised in-process)
    and its cross-process torn-tmp residual is a documented
    follow-up, not a shipped change."""

    def test_no_ignored_save_json_in_system6(self):
        # INTENTIONAL (skills split Phase A batch A24, system6):
        # the _save_json call sites moved with the tools into the
        # impl modules, so this AST pin follows the moved source
        # (diag holds the Wi-Fi calls, devices the peripheral and
        # screen-time calls); reading only the facade would pass
        # vacuously, since the facade holds no calls at all.
        # The pin asserts at least one call site is actually
        # checked, so a vacuous pass cannot hide a regression.
        checked = 0
        for rel in ("core/skills_system6_diag.py",
                    "core/skills_system6_devices.py"):
            src = (TREE / rel).read_text(encoding="utf-8")
            tree = ast.parse(src)
            parents = {}
            for node in ast.walk(tree):
                for child in ast.iter_child_nodes(node):
                    parents[child] = node
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "_save_json"):
                    checked += 1
                    parent = parents.get(node)
                    self.assertIsNot(
                        parent, None, "_save_json call without a parent")
                    # A bare statement call ignores the return; an
                    # `if not ...` test or an assignment checks it.
                    self.assertNotIsInstance(
                        parent, ast.Expr,
                        "_save_json return ignored at line "
                        f"{node.lineno} in {rel}")
        self.assertGreater(checked, 0,
                           "no _save_json call sites found in the "
                           "system6 impl modules")

    def test_snippet_lock_spans_save_and_delete(self):
        seg = _fn_source(str(TREE / "core" / "skills_productivity.py"),
                         "save_snippet")
        self.assertIn("with _STORE_LOCK", seg)
        seg = _fn_source(str(TREE / "core" / "skills_productivity.py"),
                         "delete_snippet")
        self.assertIn("with _STORE_LOCK", seg)
        mod = (TREE / "core" / "skills_productivity.py").read_text(
            encoding="utf-8")
        self.assertIn("_STORE_LOCK = threading.RLock()", mod)

    def test_unique_tmp_in_fixed_stores(self):
        cases = [
            ("core/skills_productivity.py", "_save_store", None),
            ("core/scheduler.py", "_save", "ProactiveScheduler"),
            ("core/funnel/tool_operations.py", "_write_file", None),  # Phase 3 F2a INTENTIONAL: tool_operations moved to core/funnel/
            ("core/brain/planner_storage.py", "_save_list", None),  # INTENTIONAL (skills split B40): _save_list moved to core/brain/planner_storage.py; same function, same unique-tmp assertion
            ("core/confirm_gate_allowlist.py", "_save_allowlist", None),  # INTENTIONAL (skills split B18): _save_allowlist moved to core/confirm_gate_allowlist.py; same function, same unique-tmp assertion
            ("core/memory/memory_files.py", "write_fact", None),  # Phase 3 M1b follow-up INTENTIONAL: memory_files moved to core/memory/ in M1b; the test's case list was missed then, proven by identical old paths on the B1a-2 backup tree
        ]
        for rel, func, cls in cases:
            seg = _fn_source(str(TREE / rel), func, cls)
            _assert_unique_tmp(self, seg, f"{rel}:{func}")

    def test_already_safe_stores_use_mkstemp(self):
        seg = _fn_source(str(TREE / "core" / "brain" / "task_ledger.py"),
                         "_save", "TaskLedger")  # Phase 3 B1a-2 INTENTIONAL: moved to core/brain/
        self.assertIn("mkstemp", seg)
        seg = _fn_source(str(TREE / "core" / "memory" / "playbook_store.py"),
                         "save_playbook")  # Phase 3 M1b follow-up INTENTIONAL: playbook_store moved to core/memory/ in M1b; the test's path was missed then, proven by identical old paths on the B1a-2 backup tree
        self.assertIn("mkstemp", seg)

    def test_scheduler_concurrent_saves_stay_valid(self):
        tmpdir = tempfile.TemporaryDirectory(prefix="f01f02_sched_")
        self.addCleanup(tmpdir.cleanup)
        sched = ProactiveScheduler(
            store_path=Path(tmpdir.name) / "sched.json")
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait(timeout=30)
            for _ in range(20):
                sched._save()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        data = json.loads((Path(tmpdir.name) / "sched.json")
                          .read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)
        leftovers = [p.name for p in Path(tmpdir.name).iterdir()
                     if p.name != "sched.json"]
        self.assertEqual(leftovers, [])

    def test_ops_and_planner_concurrent_writes_stay_valid(self):
        tmpdir = tempfile.TemporaryDirectory(prefix="f01f02_ops_")
        self.addCleanup(tmpdir.cleanup)
        base = Path(tmpdir.name)

        def ops_worker(tag):
            for i in range(20):
                ops_write_file(base / "ops.json", {"tag": tag, "i": i})

        def planner_worker(tag):
            for i in range(20):
                planner_save_list(base / "plans.json",
                                  [{"tag": tag, "i": i}])

        for worker in (ops_worker, planner_worker):
            threads = [threading.Thread(target=worker, args=(t,))
                       for t in ("m", "n")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=60)
        self.assertIsInstance(json.loads(
            (base / "ops.json").read_text(encoding="utf-8")), dict)
        self.assertIsInstance(json.loads(
            (base / "plans.json").read_text(encoding="utf-8")), list)
        leftovers = sorted(p.name for p in base.iterdir()
                           if p.name not in ("ops.json", "plans.json"))
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
