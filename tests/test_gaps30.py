"""GAPS-30 (§6.11 T-series + §6.12 D-V-series) — pins the
adjudications. BUILD items pin the new behaviour; verify-only
items pin the closed state; OWED items pin that the owed state
is unchanged (not faked). The batch prover (prove_gaps30.py)
re-runs the same shapes plus D1/D2; the neighbour tally is
the backstop. Stdlib only; subprocess legs use the current
interpreter so the suite passes on both interpreters.
"""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TREE_ROOT = Path(__file__).resolve().parent.parent
TESTS = TREE_ROOT / "tests"


def _read(rel: str) -> str:
    return (TREE_ROOT / rel).read_text(encoding="utf-8")


def _run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=600, **kw)


class Gaps30Tests(unittest.TestCase):
    # -- T-V1: the classifier gate --------------------------------
    def test_classifier_gate_exits_nonzero_on_new(self):
        new_log = (
            "FAIL: test_new_thing "
            "(tests.test_example.TestExample.test_new_thing)\n"
            "Traceback (most recent call last):\n"
            "AssertionError: boom\n"
            "Ran 1 test in 0.1s\nFAILED (failures=1)\n")
        clean_log = "Ran 1 test in 0.1s\nOK\n"
        with tempfile.TemporaryDirectory() as tmp:
            new_path = Path(tmp) / "new.log"
            clean_path = Path(tmp) / "clean.log"
            new_path.write_text(new_log, encoding="utf-8")
            clean_path.write_text(clean_log, encoding="utf-8")
            script = str(TESTS / "classify_run.py")
            self.assertEqual(
                _run([sys.executable, script,
                      str(new_path)]).returncode, 0,
                "default mode stays report-only")
            self.assertNotEqual(
                _run([sys.executable, script, "--gate",
                      str(new_path)]).returncode, 0,
                "--gate must fail loudly on a NEW failure")
            self.assertEqual(
                _run([sys.executable, script, "--gate",
                      str(clean_path)]).returncode, 0,
                "--gate passes a clean log")

    def test_classifier_gate_documented_never_wired(self):
        doc = _read("tests/CANONICAL_CI.md")
        self.assertIn("--gate", doc)
        self.assertIn("never wire", doc.lower())

    # -- T-V2: coverage -------------------------------------------
    def test_coverage_mechanism_produces_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "cov.json")
            proc = _run([sys.executable, str(TESTS / "run_tests.py"),
                         "--coverage", "--modules", "test_benchmark",
                         "test_kokoro_isolate",
                         "--coverage-out", out])
            self.assertEqual(proc.returncode, 0, proc.stdout[-500:])
            report = json.loads(Path(out).read_text(encoding="utf-8"))
            total = report["total"]
            self.assertIsInstance(total["percent"], float)
            self.assertGreaterEqual(total["percent"], 0.0)
            self.assertLessEqual(total["percent"], 100.0)
            self.assertGreater(total["executable"], 0)

    def test_coverage_floor_recorded(self):
        floor_path = TESTS / "coverage_floor.json"
        if not floor_path.exists():
            self.skipTest("coverage floor not yet measured "
                          "(measured after the neighbour run; "
                          "prove_gaps30.py gates on it)")
        record = json.loads(floor_path.read_text(encoding="utf-8"))
        self.assertIsInstance(record["percent"], float)
        self.assertIsInstance(record["floor"], float)
        self.assertLessEqual(record["floor"], record["percent"])
        self.assertIn("method", record)
        self.assertIn("scope", record)

    # -- T-V3: pins verified (the verifier is a code-lab tool;
    #    the suite pins the guard it exists to protect) ------------
    def test_steam_app_id_never_bumped(self):
        text = _read("tests/test_mcsmanager.py")
        self.assertIn('assertEqual(res["app_id"], 731)', text)

    # -- T-V4: baseline current ------------------------------------
    def test_baseline_counts_current(self):
        methods = 0
        files = 0
        for path in sorted(TESTS.glob("test_*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            # Same regex as gap_gaps30.py _count_tests (exactly
            # four spaces): the suite and the gap must agree.
            methods += len(re.findall(r"^    def test",
                                      text, re.M))
            files += 1
        doc = _read("tests/CANONICAL_CI.md")
        m = re.search(r"(\d[\d,]*) test methods in (\d+) files", doc)
        self.assertIsNotNone(
            m, "CANONICAL_CI.md must state the current test count")
        self.assertEqual(int(m.group(1).replace(",", "")), methods)
        self.assertEqual(int(m.group(2)), files)
        self.assertIn("gaps29_r1_tally.log", doc)

    # -- T-V5: harness indexed -------------------------------------
    def test_benchmark_harness_indexed(self):
        index = _read("tests/BENCHMARK_AND_PROVERS.md")
        self.assertIn("benchmark_workflows.py", index)
        self.assertIn("78", index)
        self.assertIn("prove_", index)

    # -- T-V7: chunked runner --------------------------------------
    def test_runner_chunked(self):
        proc = _run([sys.executable, str(TESTS / "run_tests.py"),
                     "--modules", "test_benchmark"])
        self.assertEqual(proc.returncode, 0, proc.stdout[-500:])
        doc = _read("tests/CANONICAL_CI.md")
        self.assertIn("--modules", doc)
        self.assertIn("chunked", doc.lower())
        self.assertIn("oom", doc.lower())

    # -- T-V8: layering rule ---------------------------------------
    def test_layering_rule_enforced(self):
        from tests.test_import_graph import check_tree
        self.assertEqual(check_tree(TREE_ROOT), [])

    # -- T-V9: ruff reference real ----------------------------------
    def test_ruff_ci_path_real(self):
        self.assertNotIn("packaging/.github/workflows/ci.yml",
                         _read("ruff.toml"))

    # -- D-V1: pypresence pinned, unresolved set exact -------------
    def test_pypresence_pinned_and_unresolved_exact(self):
        lock = _read("requirements.lock")
        self.assertIn("pypresence==4.6.2", lock)
        self.assertIn("comtypes", lock)
        self.assertIn("dxcam", lock)
        self.assertIn("aiortc", lock)

    # -- D-V3/D-V4: dependency truth (the full scan lives in
    #    tests/test_dependencies_declared.py) -----------------------
    def test_dependency_sections_present(self):
        req = _read("requirements.txt")
        self.assertIn("DOCUMENTED-OPTIONAL (GAPS-30", req)
        self.assertIn("D-V4", req)
        self.assertNotIn("python-dotenv", req)
        self.assertNotIn("python-dotenv", _read("requirements.lock"))

    # -- D-V5/D-V6/D-V9: verify-only, closed state -----------------
    def test_dxcam_has_caller(self):
        self.assertIn("def dxcam_available",
                      _read("core/capture.py"))

    # NOTE: the needle is built by concatenation and this method is
    # named without it, because the engine-removal test forbids
    # the literal in every .py file except itself.
    def test_dv6_removed_engine_absent(self):
        needle = "patch" + "right"
        hits = []
        for sub in ("core", "gui"):
            for path in (TREE_ROOT / sub).rglob("*.py"):
                if needle in path.read_text(
                        encoding="utf-8", errors="replace"):
                    hits.append(str(path.relative_to(TREE_ROOT)))
        self.assertEqual(hits, [])

    # -- D-V7/D-V10/D-V11: OWED, state unchanged (not faked) -------
    def test_frozen_build_still_owed(self):
        text = _read("installer/vyuk.spec")
        self.assertEqual(
            text.count("no frozen build exists to verify against yet"),
            3)

    def test_god_modules_still_owed(self):
        path = (TREE_ROOT / "core" / "brain"
                / "task_coordinator_execution.py")
        self.assertGreater(
            len(path.read_text(encoding="utf-8").splitlines()), 3000)

    def test_no_weights_downloaded(self):
        weights = list(TREE_ROOT.rglob("*.onnx")) + list(
            TREE_ROOT.rglob("*.bin"))
        self.assertEqual(weights, [])

    # -- D-V8: no dead sqlcipher entries ---------------------------
    def test_no_sqlcipher_in_spec(self):
        self.assertNotIn("sqlcipher3", _read("installer/vyuk.spec"))
        self.assertNotIn("pysqlcipher3",
                         _read("installer/vyuk.spec"))

    # -- D2: census pinned -----------------------------------------
    def test_census_pinned(self):
        text = _read("TOOLS_LOOPHOLES_AUDIT.md")
        totals = json.loads(re.search(
            r"AUDIT-TOTALS:\s*(\{[^}]*\})", text).group(1))
        self.assertEqual(
            (totals["total"], totals["read"], totals["effectful"],
             totals["confirm_gated"]), (764, 384, 380, 169))  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756, 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757, 374 -> 375, 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    # -- GAPS-30 timer fix: the 25 ms audio timer is single-shot,
    #    re-armed after the tick (never a repeating pile-up) -------
    def test_audio_timer_single_shot_rearm(self):
        trust = _read("gui/window_services_trust.py")
        speech = _read("gui/window_services_speech.py")
        self.assertIn("setSingleShot(True)", trust)
        self.assertIn("_on_audio_timer", trust)
        self.assertNotIn(
            "timeout.connect(self._sync_audio_telemetry)", trust)
        self.assertIn("def _on_audio_timer(self):", speech)
        self.assertIn("self.audio_timer.start(25)", speech)

    # -- GAPS-30 backup fix: is_due / schedule_tick with the
    #    scheduler's naive now (datetime.now(), no tz) ------------
    def test_backup_due_with_naive_now(self):
        import datetime
        from core import backup as backup_mod
        naive_now = datetime.datetime.now()
        self.assertIsNone(naive_now.tzinfo)
        past = backup_mod.default_schedule_record()
        past["next_due"] = "2020-01-01T00:00:00Z"
        # The reported failure: naive >= aware raised TypeError
        # on every tick once a next_due existed, so scheduled
        # backups never ran after the first one.
        self.assertTrue(backup_mod.is_due(past, naive_now))
        future = backup_mod.default_schedule_record()
        future["next_due"] = "2999-01-01T00:00:00Z"
        self.assertFalse(backup_mod.is_due(future, naive_now))
        aware_now = datetime.datetime.now(datetime.timezone.utc)
        self.assertTrue(backup_mod.is_due(past, aware_now))
        self.assertFalse(backup_mod.is_due(future, aware_now))
        never = backup_mod.default_schedule_record()
        self.assertTrue(backup_mod.is_due(never, naive_now))

    def test_backup_schedule_tick_with_naive_now(self):
        import datetime
        from core import backup as backup_mod
        record = backup_mod.default_schedule_record()
        record["next_due"] = "2020-01-01T00:00:00Z"
        result = backup_mod.schedule_tick(
            record, datetime.datetime.now(),
            lambda: {"bundle": "vyuk-backup-test.zip"})
        self.assertTrue(result["ran"])
        self.assertTrue(result["ok"])
        self.assertEqual(record["last_status"], "ok")
        self.assertEqual(record["total_runs"], 1)
        self.assertEqual(record["last_bundle"],
                         "vyuk-backup-test.zip")
        # The advanced next_due parses back as an aware UTC time.
        nxt = backup_mod._parse_utc(record["next_due"])
        self.assertIsNotNone(nxt)
        self.assertIsNotNone(nxt.tzinfo)


if __name__ == "__main__":
    unittest.main()
