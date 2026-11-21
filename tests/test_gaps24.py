"""GAPS-24 suite — K-series (review §6.4): error handling /
failsafe / kill-chain.

K-V1, K-V2, K-V5, K-V7 are already-closed pins (GAPS-12 / GAPS-13 /
GAPS-22): the suite pins their fail-closed behaviour so a later
refactor cannot silently reopen them. K-V3, K-V6, K-V8 are the
build: the headless stop-flag, the live-dispatch token registry +
trip wiring, the kill-chain state cache, and the kill primitive.
K-V4's reconciliation mechanism (REL-03) is pinned end to end;
the reconciliation TOOL surface is owed, not built (no new tool
in this batch) — test_reconciliation_tool_still_owed records that.

Stdlib unittest only. Run via tests/run_tests.py or the neighbour
runner. Tests that call execute() write receipts/audit/activity
into the tree's data/; tearDownModule restores data/ from the
source backup, so the suite is self-cleaning.
"""

import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_SRC_DATA = (pathlib.Path.home() / "workspace" / "code-lab"
             / ".brainproof" / "data_backup_v529")
_TREE = pathlib.Path(__file__).resolve().parent.parent
_DATA = _TREE / "data"


def setUpModule():
    pass


def tearDownModule():
    if _DATA.exists():
        shutil.rmtree(_DATA)
    shutil.copytree(_SRC_DATA, _DATA)


import core.funnel.kill_chain as kc  # noqa: E402
import core.funnel.tool_cancel as tc  # noqa: E402
import core.funnel.tool_operations as ops  # noqa: E402
import core.tool_receipts as receipts  # noqa: E402
from core import confirm_gate as cg  # noqa: E402
from core import failsafe as fs  # noqa: E402
from core.funnel.guard_state import FailsafeState  # noqa: E402
from core.funnel.tools_gate import (  # noqa: E402
    _CONFIRM_GATE_TOOLS, _is_read_class)
from core.funnel.tools_registry import tools as TOOLS  # noqa: E402
from core.funnel.tools_registry_validators import _failsafe_state  # noqa: E402


def _read_tool():
    for cand in ("clock", "get_date", "list_timezones"):
        spec = TOOLS._tools.get(cand)
        if spec is None:
            continue
        req = (spec.get("parameters") or {}).get("required") or []
        if _is_read_class(cand, {}) and cand not in _CONFIRM_GATE_TOOLS \
                and not req:
            return cand
    for name in sorted(TOOLS._tools):
        spec = TOOLS._tools[name]
        req = (spec.get("parameters") or {}).get("required") or []
        if _is_read_class(name, {}) and name not in _CONFIRM_GATE_TOOLS \
                and not req:
            return name
    raise AssertionError("no read tool callable with {}")


def _run_long_command(results, command="sleep 30"):
    results["out"] = TOOLS.execute("run_command", {"command": command})
    results["done"] = time.monotonic()


class KillChainCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="g24_t_kc_"))
        kc.configure(self.tmp)
        (self.tmp / "kill_chain.json").write_text(
            json.dumps({"rung": 0, "reason": "", "set_at": ""}),
            encoding="utf-8")

    def tearDown(self):
        kc.reset_configuration()
        kc.reset_cache_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rewrite(self, rung):
        (self.tmp / "kill_chain.json").write_text(
            json.dumps({"rung": rung, "reason": "test",
                        "set_at": "2026-11-14T00:00:00"}),
            encoding="utf-8")
        os.utime(self.tmp / "kill_chain.json",
                 ns=(time.time_ns(), time.time_ns()))

    def test_gate_reads_file_once_for_many_calls(self):
        reads = {"n": 0}
        orig = pathlib.Path.read_text

        def counting(self, *a, **k):
            if self.name == "kill_chain.json":
                reads["n"] += 1
            return orig(self, *a, **k)
        pathlib.Path.read_text = counting
        try:
            for _ in range(20):
                self.assertIsNone(kc.gate_tool("clock", True, False))
        finally:
            pathlib.Path.read_text = orig
        self.assertLessEqual(reads["n"], 2, f"reads={reads['n']}")

    def test_external_escalation_is_seen(self):
        self._rewrite(2)
        self.assertIsNotNone(kc.gate_tool("web_search", False, True))
        self.assertIsNone(kc.gate_tool("clock", True, False))
        self._rewrite(5)
        self.assertIsNotNone(kc.gate_tool("clock", True, False))

    def test_external_lower_is_seen(self):
        self._rewrite(5)
        self.assertIsNotNone(kc.gate_tool("clock", True, False))
        self._rewrite(0)
        self.assertIsNone(kc.gate_tool("clock", True, False))

    def test_corrupt_state_raises_strict_and_reads_zero_lenient(self):
        (self.tmp / "kill_chain.json").write_text("{not json",
                                                  encoding="utf-8")
        os.utime(self.tmp / "kill_chain.json",
                 ns=(time.time_ns(), time.time_ns()))
        with self.assertRaises(kc._KillChainUnavailable):
            kc.gate_tool("clock", True, False)
        self.assertEqual(kc.current_rung(), 0)

    def test_fixed_file_is_picked_up(self):
        (self.tmp / "kill_chain.json").write_text("{not json",
                                                  encoding="utf-8")
        os.utime(self.tmp / "kill_chain.json",
                 ns=(time.time_ns(), time.time_ns()))
        with self.assertRaises(kc._KillChainUnavailable):
            kc.gate_tool("clock", True, False)
        self._rewrite(0)
        self.assertIsNone(kc.gate_tool("clock", True, False))

    def test_escalate_lower_through_cache(self):
        kc.escalate(2, "test")
        self.assertEqual(kc.current_rung(), 2)
        self.assertIsNotNone(kc.gate_tool("web_search", False, True))
        self.assertEqual(kc.status()["rung"], 2)
        kc.lower(0, "test")
        self.assertEqual(kc.current_rung(), 0)
        self.assertIsNone(kc.gate_tool("web_search", False, True))

    def test_missing_file_reads_zero_and_created_file_seen(self):
        (self.tmp / "kill_chain.json").unlink()
        kc.reset_cache_for_tests()
        self.assertEqual(kc.current_rung(), 0)
        self.assertIsNone(kc.gate_tool("clock", True, False))
        self._rewrite(1)
        self.assertIsNotNone(kc.gate_tool("write_file", False, False))

    def test_configure_redirect_invalidates(self):
        other = pathlib.Path(tempfile.mkdtemp(prefix="g24_t_kc2_"))
        try:
            (other / "kill_chain.json").write_text(
                json.dumps({"rung": 0, "reason": "", "set_at": ""}),
                encoding="utf-8")
            self._rewrite(5)
            self.assertIsNotNone(kc.gate_tool("clock", True, False))
            kc.configure(other)
            self.assertIsNone(kc.gate_tool("clock", True, False))
        finally:
            shutil.rmtree(other, ignore_errors=True)


class FailsafeHeadlessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="g24_t_fs_"))
        self.flag = self.tmp / "nova_stop.flag"
        self._old_flag = fs._stop_flag_path

    def tearDown(self):
        fs._stop_flag_path = self._old_flag
        fs.reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _arm_flag(self):
        self.flag.write_text("", encoding="utf-8")
        fs.configure(stop_flag_path=self.flag)

    def test_stop_flag_present_helper(self):
        self.assertFalse(fs.stop_flag_present())
        self._arm_flag()
        self.assertTrue(fs.stop_flag_present())

    def test_consult_tripped_with_flag(self):
        self._arm_flag()
        state, reason = _failsafe_state()
        self.assertIs(state, FailsafeState.TRIPPED)
        self.assertIn("stop flag", reason.lower())

    def test_flag_refuses_non_read_call(self):
        self._arm_flag()
        out = TOOLS.execute("run_command", {"command": "echo hi"})
        self.assertEqual(out.get("kind"), "refused")
        self.assertIn("failsafe", str(out.get("message", "")).lower())

    def test_flag_does_not_block_reads(self):
        self._arm_flag()
        out = TOOLS.execute(_read_tool(), {})
        self.assertEqual(out.get("status"), "success")

    def test_flag_observation_cancels_running_work(self):
        # No trip_now here: the flag alone, observed by a later
        # (read) call's failsafe stage, must stop the running work.
        # The command's sleep length is unique to this test: a
        # cancelled dispatch leaves a receipt-guard hit, and an
        # identical retry would be refused by the guard instead
        # of running (by design — see ReconciliationTests).
        cg.register_handler(lambda n, s: True)
        results: dict = {}
        try:
            th = threading.Thread(target=_run_long_command,
                                  args=(results, "sleep 30"),
                                  daemon=True)
            t0 = time.monotonic()
            th.start()
            time.sleep(1.0)
            self._arm_flag()
            out = TOOLS.execute(_read_tool(), {})
            self.assertEqual(out.get("status"), "success")
            th.join(timeout=20.0)
            self.assertFalse(th.is_alive(), "run_command kept running")
            self.assertLess(time.monotonic() - t0, 20.0)
            self.assertEqual(results["out"].get("status"), "cancelled")
        finally:
            cg.clear_handler()


class LiveDispatchRegistryTests(unittest.TestCase):
    def tearDown(self):
        for call in tc.live_calls():
            tc.unregister_live_call(call["handle"])

    def test_register_cancel_unregister(self):
        tok = tc.CancellationToken()
        handle = tc.register_live_call(tok, "run_command")
        self.assertGreater(handle, 0)
        self.assertFalse(tok.cancelled)
        self.assertTrue(tc.unregister_live_call(handle))
        self.assertFalse(tc.unregister_live_call(handle))

    def test_cancel_all_live(self):
        a, b = tc.CancellationToken(), tc.CancellationToken()
        tc.register_live_call(a, "winget_install")
        tc.register_live_call(b, "download_file")
        self.assertEqual(tc.cancel_all_live(reason="stop"), 2)
        self.assertTrue(a.cancelled and b.cancelled)

    def test_cancel_live_call_single(self):
        a, b = tc.CancellationToken(), tc.CancellationToken()
        ha = tc.register_live_call(a, "winget_install")
        tc.register_live_call(b, "download_file")
        self.assertTrue(tc.cancel_live_call(ha, reason="stop"))
        self.assertTrue(a.cancelled)
        self.assertFalse(b.cancelled)
        self.assertFalse(tc.cancel_live_call(999999))

    def test_live_calls_listing(self):
        self.assertEqual(tc.live_calls(), [])
        tok = tc.CancellationToken()
        handle = tc.register_live_call(tok, "run_command")
        listed = tc.live_calls()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["handle"], handle)
        self.assertEqual(listed[0]["tool"], "run_command")

    def test_trip_listener_cancels(self):
        tok = tc.CancellationToken()
        handle = tc.register_live_call(tok, "run_command")
        try:
            tc.ensure_trip_cancels_live_calls()
            fs.trip_now("suite probe")
            self.assertTrue(tok.cancelled)
        finally:
            fs.reset()
            tc.unregister_live_call(handle)

    def test_no_leak_after_completed_execute(self):
        self.assertEqual(tc.live_calls(), [])
        out = TOOLS.execute(_read_tool(), {})
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(tc.live_calls(), [])

    def test_dispatch_registers_token_and_cancel_stops_it(self):
        # Sleep length unique to this test (see the flag test's
        # note: the receipt guard blocks identical retries of a
        # cancelled dispatch until a human reconciles it).
        cg.register_handler(lambda n, s: True)
        results: dict = {}
        try:
            th = threading.Thread(target=_run_long_command,
                                  args=(results, "sleep 31"),
                                  daemon=True)
            t0 = time.monotonic()
            th.start()
            handle = None
            for _ in range(50):
                for call in tc.live_calls():
                    if call["tool"] == "run_command":
                        handle = call["handle"]
                if handle is not None:
                    break
                time.sleep(0.1)
            self.assertIsNotNone(handle,
                                 "dispatch never registered its token")
            self.assertTrue(tc.cancel_live_call(handle, reason="stop"))
            th.join(timeout=20.0)
            self.assertFalse(th.is_alive(), "run_command kept running")
            self.assertLess(time.monotonic() - t0, 20.0)
            self.assertEqual(results["out"].get("status"), "cancelled")
        finally:
            cg.clear_handler()


class TripStopsWorkTests(unittest.TestCase):
    def tearDown(self):
        fs.reset()

    def test_trip_stops_run_command(self):
        # Sleep length unique to this test (see the flag test's
        # note on the receipt guard).
        cg.register_handler(lambda n, s: True)
        results: dict = {}
        try:
            th = threading.Thread(target=_run_long_command,
                                  args=(results, "sleep 32"),
                                  daemon=True)
            t0 = time.monotonic()
            th.start()
            time.sleep(1.0)
            fs.trip_now("suite probe: stop the running command")
            th.join(timeout=20.0)
            self.assertFalse(th.is_alive(), "run_command kept running")
            self.assertLess(time.monotonic() - t0, 20.0)
            self.assertEqual(results["out"].get("status"), "cancelled")
        finally:
            cg.clear_handler()

    def test_cancelled_run_reports_cancelled_not_success(self):
        # The race GAPS-24 caught: a child killed from outside
        # (the trip's proc-kill) lands via communicate()
        # returning, not via the token-poll branch — the report
        # must be cancelled, never "Exit code -9" as a success.
        from core.desktop import control_shell as cs
        tok = tc.CancellationToken()
        tok.cancel(reason="stop")
        out = cs.run_command("echo hi", cancel_token=tok)
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "cancelled")

    def test_run_with_cancel_reports_cancelled_when_token_cancelled(self):
        tok = tc.CancellationToken()
        tok.cancel(reason="stop")
        rc, _out, _err = tc.run_with_cancel(["true"], 10, tok)
        self.assertEqual(rc, -3)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="g24_t_rc_"))
        receipts.install_store(self.tmp / "tool_receipts.jsonl")
        ops.set_store({})
        self.tool, self.args = "write_file", {"path": "/tmp/g24_x.txt",
                                             "content": "hi"}

    def tearDown(self):
        receipts.reset_default()
        ops.reset_store()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _unknown_prior(self):
        att = receipts.try_record_attempt(tool=self.tool, args=self.args)
        self.assertTrue(receipts.try_mark_dispatched(att))
        return att

    def test_guard_blocks_identical_retry(self):
        self._unknown_prior()
        self.assertIsNotNone(
            receipts.try_find_unknown(tool=self.tool, args=self.args))

    def test_did_not_happen_releases_guard(self):
        self._unknown_prior()
        sig = receipts.args_signature(self.tool, self.args)
        op = ops.record_timeout(self.tool, "funnel", args_signature=sig)
        self.assertTrue(any(r["op_id"] == op
                            for r in ops.list_pending_unknown()))
        ok, detail = ops.resolve_pending_unknown(op, "did_not_happen")
        self.assertTrue(ok)
        self.assertIn("guard released", detail)
        self.assertIsNone(
            receipts.try_find_unknown(tool=self.tool, args=self.args))

    def test_happened_keeps_guard_blocking(self):
        self._unknown_prior()
        sig = receipts.args_signature(self.tool, self.args)
        op = ops.record_timeout(self.tool, "funnel", args_signature=sig)
        ok, _detail = ops.resolve_pending_unknown(op, "happened")
        self.assertTrue(ok)
        self.assertIsNotNone(
            receipts.try_find_unknown(tool=self.tool, args=self.args))

    def test_resolve_is_exactly_once(self):
        self._unknown_prior()
        sig = receipts.args_signature(self.tool, self.args)
        op = ops.record_timeout(self.tool, "funnel", args_signature=sig)
        ok, _d = ops.resolve_pending_unknown(op, "did_not_happen")
        self.assertTrue(ok)
        ok2, _d2 = ops.resolve_pending_unknown(op, "did_not_happen")
        self.assertFalse(ok2)


class RungThreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="g24_t_r3_"))
        kc.configure(self.tmp)

    def tearDown(self):
        kc.reset_configuration()
        kc.reset_cache_for_tests()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rung_three_freezes_brain(self):
        kc.escalate(3, "test")
        self.assertTrue(kc.brain_frozen())

    def test_funnel_does_not_judge_rung_three(self):
        kc.escalate(3, "test")
        self.assertIsNone(kc.gate_tool("clock", True, False))

    def test_brain_dispatch_pins_fail_closed(self):
        src = (_TREE / "core" / "brain"
               / "brain_session_core.py").read_text(encoding="utf-8")
        self.assertIn("_kc.brain_frozen()", src)
        self.assertIn("engine = Engine.LOCAL", src)
        seg = src[src.index("brain_frozen"):
                  src.index("brain_frozen") + 1200]
        self.assertIn("except Exception", seg)


class FailClosedRegressionTests(unittest.TestCase):
    def test_rule_of_two_exception_refuses(self):
        from core.funnel import rule_of_two as rot
        orig = rot.legs_for_native_call

        def _boom(*a, **k):
            raise RuntimeError("budget store exploded")
        rot.legs_for_native_call = _boom
        try:
            out = TOOLS.execute(_read_tool(), {})
        finally:
            rot.legs_for_native_call = orig
        self.assertEqual(out.get("kind"), "refused")
        self.assertIn("Rule-of-Two", str(out.get("message", "")))

    def test_staleness_raise_and_unchecked_refuse(self):
        from core.desktop import live_staleness as ls
        from core.funnel import live_origin as lo
        cands = [
            ("mouse_control", {"action": "move", "x": 100, "y": 100}),
            ("mouse_control", {"action": "click", "x": 100, "y": 100}),
            ("cursor_control", {"action": "move_to", "x": 100, "y": 100}),
        ]
        tool, args = next((t, a) for t, a in cands
                          if ls.is_live_point_action(t, a))
        orig = ls.check_staleness

        def _boom(*a, **k):
            raise RuntimeError("grab failed")
        ls.check_staleness = _boom
        try:
            tok = lo.set_live_origin()
            try:
                out_a = TOOLS.execute(tool, dict(args))
            finally:
                lo.reset_live_origin(tok)
        finally:
            ls.check_staleness = orig
        self.assertEqual(out_a.get("kind"), "stale-view")
        ls.check_staleness = lambda **k: {"verdict": "unchecked",
                                          "reason": "no baseline yet"}
        try:
            tok = lo.set_live_origin()
            try:
                out_b = TOOLS.execute(tool, dict(args))
            finally:
                lo.reset_live_origin(tok)
        finally:
            ls.check_staleness = orig
        self.assertEqual(out_b.get("kind"), "stale-view")

    def test_echo_guard_unconsultable_refuses(self):
        import core as core_pkg
        import core.echo_guard  # noqa: F401
        sys.modules["core.echo_guard"] = None
        if hasattr(core_pkg, "echo_guard"):
            delattr(core_pkg, "echo_guard")
        try:
            out = TOOLS.execute("type_text", {"text": "hello world"})
        finally:
            del sys.modules["core.echo_guard"]
            import core.echo_guard  # noqa: F401
        self.assertEqual(out.get("kind"), "refused")
        self.assertIn("echo guard", str(out.get("message", "")).lower())

    def test_epoch_unimportable_refuses(self):
        import core as core_pkg
        import core.session_epoch  # noqa: F401
        sys.modules["core.session_epoch"] = None
        if hasattr(core_pkg, "session_epoch"):
            delattr(core_pkg, "session_epoch")
        try:
            out = TOOLS.execute(_read_tool(), {})
        finally:
            del sys.modules["core.session_epoch"]
            import core.session_epoch  # noqa: F401
        self.assertEqual(out.get("kind"), "refused")
        self.assertIn("approval void", str(out.get("message", "")).lower())


class AuditSinkTests(unittest.TestCase):
    def test_receipt_store_failure_refuses_non_read(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="g24_t_as_"))
        try:
            receipts.install_store(tmp)  # a directory, not a file
            before = receipts.guard_failure_count()
            out = TOOLS.execute("write_file",
                                {"path": "/tmp/g24_kv8c.txt",
                                 "content": "hi"})
            self.assertGreater(receipts.guard_failure_count(), before)
            self.assertEqual(out.get("kind"), "refused")
            self.assertIn("receipt", str(out.get("message", "")).lower())
        finally:
            receipts.reset_default()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_audit_failure_degrades_never_refuses(self):
        import core.audit as audit_mod
        orig = audit_mod.write_record

        def _boom(*a, **k):
            raise RuntimeError("audit disk full")
        audit_mod.write_record = _boom
        try:
            out = TOOLS.execute(_read_tool(), {})
        finally:
            audit_mod.write_record = orig
        self.assertEqual(out.get("status"), "success")
        self.assertEqual(out.get("observability"), "degraded")
        self.assertIn("audit", out.get("observability_failures", []))


class CensusTests(unittest.TestCase):
    def test_census_unchanged(self):
        decls = TOOLS.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 758)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(len(set(names)), 758)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        params = sum(len(d["parameters"].get("properties", {}))
                   for d in decls)
        self.assertEqual(params, 2079)  #Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 757)  # INTENTIONAL — field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing
        from core import catalogue_groups_data as cat
        grouped = sum(len(m) for m in cat.GROUP_MEMBERS.values())
        self.assertEqual(grouped, 755)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 167)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated

    def test_reconciliation_tool_still_owed(self):
        # No new tool in this batch: the reconciliation mechanism
        # exists (tool_operations.resolve_pending_unknown), but the
        # tool surface is a decision owed. If a later batch adds
        # the tool, this pin must be updated with it.
        names = set(TOOLS._tools)
        recon = [n for n in names
                 if "reconcil" in n or n.startswith("resolve_operation")
                 or n == "pending_operations"]
        self.assertEqual(recon, [])


if __name__ == "__main__":
    unittest.main()
