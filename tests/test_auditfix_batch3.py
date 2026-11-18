"""Audit fix Batch 3 — RECEIPTS / OBSERVABILITY / REGISTRY.

Covers REL-01 + OBS-01 (one ledger PR), REG-01, REL-02, REL-03
(scoped), CAP-01 (labelling only), COV-02 (the 5 effectful
tools only) and REL-04 (critical-tier fsync only).

Every test that drives the funnel installs a SCRATCH receipt
store (temp dir) and an in-memory operation ledger, so no test
writes receipts into data/; the audit/activity logs the funnel
still emits are restored by the batch's data-restore discipline.
Taint is cleared in a finally block wherever a suite could
touch _tool_screen_look (none of these tests do, but the base
class clears it anyway, per the standing TAINT LESSON).
"""

import json
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from core import confirm_gate
from core import failsafe
from core import injection
from core.funnel import tool_cancel as cancel_mod  # Phase 3 F2a INTENTIONAL: tool_cancel moved to core/funnel/
from core.funnel import tool_operations as ops  # Phase 3 F2a INTENTIONAL: tool_operations moved to core/funnel/
from core import tool_receipts as receipts
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel import tools_handlers as handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel import tools_registry as registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

TREE = Path(__file__).resolve().parent.parent

CAP01_EXPECTED = {
    # planner: the description frames the output as a plan
    "travel_door_to_door": "planner",
    "hardware_plan": "planner",
    "pilot_revision_plan": "planner",
    # descriptor: everything else in the audit register's set
    "game_macro_describe": "descriptor",
    "game_session_describe": "descriptor",
    "hardware_inventory": "descriptor",
    "iot_local_device": "descriptor",
    "net_network_descriptor": "descriptor",
    "net_network_status": "descriptor",
    "pilot_bench": "descriptor",
    "pilot_build_steps": "descriptor",
    "pilot_dev_workflow": "descriptor",
    "pilot_review_note": "descriptor",
    "pilot_study_session": "descriptor",
    "travel_loadout": "descriptor",
    "travel_recipe": "descriptor",
    "travel_scene": "descriptor",
    "travel_scoreboard": "descriptor",
    "travel_sonify": "descriptor",
    "travel_spatial": "descriptor",
    "travel_transport": "descriptor",
    "travel_trip": "descriptor",
    "well_reminder": "descriptor",
    "forgiving_select": "descriptor",
    "phase5_interface_describe": "descriptor",
}

COV02_FIVE = ["browser_read", "list_app_updates", "ping_host",
              "ssl_cert_info", "trace_route"]

COV02_OTHER_17 = ["battery_health", "bluetooth_devices", "disk_health",
                   "hardware_info", "list_drivers", "list_local_devices",
                   "list_network_shares", "list_printers",
                   "list_restore_points", "list_services", "list_users",
                   "power_requests", "saved_wifi_networks",
                   "security_status", "sleep_states", "system_event_log",
                   "wifi_info"]


class _FunnelBase(unittest.TestCase):
    """Fresh registry + scratch receipt store + approving
    confirm handler + saved budget/failsafe state."""

    def setUp(self):
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        confirm_gate.register_handler(lambda *a, **k: True)
        self._orig_log = confirm_gate._log_decision
        confirm_gate._log_decision = lambda *a, **k: None
        self._orig_tripped = failsafe.tripped
        self._orig_timestamps = list(handlers_mod._budget_timestamps)
        self._orig_budget_fn = handlers_mod._tool_call_budget
        self._tmp = tempfile.TemporaryDirectory()
        self.store = receipts.install_store(
            Path(self._tmp.name) / "receipts.jsonl")
        receipts.reset_receipt_degraded()
        ops.set_store({})
        self.reg = registry_mod.ToolRegistry()
        self.called = []

        def _write(**kw):
            self.called.append(kw)
            return {"ok": True}

        self.reg.register(
            "b3_probe_write", "batch3 probe mutating tool",
            {"type": "object",
             "properties": {"path": {"type": "string"}}},
            _write)
        self.reg.register(
            "read_file", "batch3 stub read",
            {"type": "object",
             "properties": {"path": {"type": "string"}}},
            lambda **kw: {"ok": True})

    def tearDown(self):
        confirm_gate._log_decision = self._orig_log
        confirm_gate.clear_handler()
        failsafe.tripped = self._orig_tripped
        handlers_mod._budget_timestamps[:] = self._orig_timestamps
        handlers_mod._tool_call_budget = self._orig_budget_fn
        receipts.reset_default()
        receipts.reset_receipt_degraded()
        ops.reset_store()
        injection.clear_taint(force=True)
        self._tmp.cleanup()
        try:
            failsafe.clear_pressed_registry()
        except Exception:
            pass


# ----------------------------------------------------------------------
# REL-01 + OBS-01
# ----------------------------------------------------------------------

class TestRel01FailClosed(_FunnelBase):
    def test_corrupt_store_refuses_nonread_and_degrades(self):
        bad = Path(self._tmp.name) / "corrupt.jsonl"
        bad.write_text('{"type": "attempt", "receipt_id": "x"}\n{corrupt\n',
                       encoding="utf-8")
        receipts.install_store(bad)
        result = self.reg.execute("b3_probe_write", {"path": "a.txt"})
        self.assertEqual(self.called, [])
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("receipt", result.get("message", "").lower())
        self.assertEqual(result.get("observability"), "degraded")
        self.assertIn("receipts",
                      result.get("observability_failures", []))
        # A read under the same broken store still runs — but
        # it carries the degraded flag too (its attempt record
        # failed), never a clean-looking success.
        result = self.reg.execute("read_file", {"path": "a.txt"})
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("observability"), "degraded")
        self.assertIn("receipts",
                      result.get("observability_failures", []))
        state = receipts.receipt_degraded_state()
        self.assertIn("guard", state)
        self.assertGreaterEqual(state["guard"]["count"], 1)

    def test_attempt_write_failure_refuses_at_dispatch(self):
        def _raise(*a, **k):
            raise RuntimeError("disk full")
        self.store.record_attempt = _raise
        result = self.reg.execute("b3_probe_write", {"path": "a.txt"})
        self.assertEqual(self.called, [])
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("receipt", result.get("message", "").lower())
        self.assertEqual(result.get("observability"), "degraded")
        # The same failure must NOT block a read.
        result = self.reg.execute("read_file", {"path": "a.txt"})
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("observability"), "degraded")

    def test_outcome_write_failure_degrades_not_refuses(self):
        def _raise(*a, **k):
            raise RuntimeError("disk full")
        self.store.record_outcome = _raise
        result = self.reg.execute("b3_probe_write", {"path": "a.txt"})
        self.assertEqual(len(self.called), 1)
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("observability"), "degraded")
        self.assertIn("receipts",
                      result.get("observability_failures", []))

    def test_healthy_store_normal_path_unchanged(self):
        result = self.reg.execute("b3_probe_write", {"path": "a.txt"})
        self.assertEqual(result.get("status"), "success")
        self.assertNotIn("observability", result)
        self.assertEqual(len(self.called), 1)
        lines = self.store.path.read_text(encoding="utf-8").splitlines()
        # Regression F1: a dispatched call writes attempt +
        # dispatched + outcome (the mark is what lets the guard
        # tell "crashed during dispatch" from "never dispatched").
        self.assertEqual(len(lines), 3)
        self.assertEqual(json.loads(lines[0])["type"], "attempt")
        self.assertEqual(json.loads(lines[1])["type"], "dispatched")
        self.assertEqual(json.loads(lines[2])["type"], "outcome")
        # A resolved call re-runs (the guard only blocks
        # unknowns).
        result = self.reg.execute("b3_probe_write", {"path": "a.txt"})
        self.assertEqual(result.get("status"), "success")

    def test_unknown_prior_still_refuses_exact(self):
        # Regression F1: only a DISPATCHED attempt guards. The
        # seed below is a call that reached dispatch and then
        # went unknown, so the guard must still refuse it.
        attempt = self.store.record_attempt(tool="b3_probe_write",
                                            args={"path": "a.txt"})
        self.assertTrue(
            self.store.mark_dispatched(attempt["receipt_id"]))
        result = self.reg.execute("b3_probe_write", {"path": "a.txt"})
        self.assertEqual(self.called, [])
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("unknown outcome", result.get("message", ""))


class TestObs01DegradedFlag(_FunnelBase):
    def test_telemetry_failure_degrades(self):
        from core import telemetry
        orig = telemetry.finish_tool_span
        telemetry.finish_tool_span = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("telemetry down"))
        try:
            result = self.reg.execute("read_file", {"path": "a.txt"})
        finally:
            telemetry.finish_tool_span = orig
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("observability"), "degraded")
        self.assertIn("telemetry",
                      result.get("observability_failures", []))
        self.assertGreaterEqual(
            registry_mod.observability_degraded_state().get(
                "telemetry", 0), 1)

    def test_audit_failure_degrades(self):
        from core import audit as audit_mod
        orig = audit_mod.write_record
        audit_mod.write_record = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("audit down"))
        try:
            result = self.reg.execute("read_file", {"path": "a.txt"})
        finally:
            audit_mod.write_record = orig
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("observability"), "degraded")
        self.assertIn("audit", result.get("observability_failures", []))

    def test_record_family_failure_degrades(self):
        from core import record_family as rf_mod
        orig = rf_mod.append_event
        rf_mod.append_event = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("chain down"))
        try:
            result = self.reg.execute("read_file", {"path": "a.txt"})
        finally:
            rf_mod.append_event = orig
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("observability"), "degraded")
        self.assertIn("record_family",
                      result.get("observability_failures", []))

    def test_clean_call_has_no_degraded_key(self):
        result = self.reg.execute("read_file", {"path": "a.txt"})
        self.assertEqual(result.get("status"), "success")
        self.assertNotIn("observability", result)


# ----------------------------------------------------------------------
# REG-01
# ----------------------------------------------------------------------

class TestReg01AllowList(unittest.TestCase):
    def _stub(self, module):
        def _handler(**kw):
            return {"ok": True}
        _handler.__module__ = "core." + module
        return _handler

    def test_undeclared_duplicate_raises_and_original_survives(self):
        reg = registry_mod.ToolRegistry()
        reg.register("dup_probe", "first",
                     {"type": "object", "properties": {}},
                     self._stub("skills_media"))
        with self.assertRaises(ValueError):
            reg.register("dup_probe", "second",
                         {"type": "object", "properties": {}},
                         self._stub("skills_media_convert"))  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertTrue(reg._tools["dup_probe"]["handler"]
                        .__module__.endswith("skills_media"))
        self.assertEqual(reg.override_log(), [])

    def test_override_true_without_allowlist_raises(self):
        reg = registry_mod.ToolRegistry()
        reg.register("dup_probe", "first",
                     {"type": "object", "properties": {}},
                     self._stub("skills_media"))
        with self.assertRaises(ValueError):
            reg.register("dup_probe", "second",
                         {"type": "object", "properties": {}},
                         self._stub("skills_media_convert"), override=True)  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertTrue(reg._tools["dup_probe"]["handler"]
                        .__module__.endswith("skills_media"))

    def test_declared_winner_replaces_and_is_logged(self):
        reg = registry_mod.ToolRegistry()
        reg.register("resize_image", "loser",
                     {"type": "object", "properties": {}},
                     self._stub("skills_media"))
        reg.register("resize_image", "winner",
                     {"type": "object", "properties": {}},
                     self._stub("skills_media_convert"), override=True)  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertTrue(reg._tools["resize_image"]["handler"]
                        .__module__.endswith("skills_media_convert"))  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        log = reg.override_log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["name"], "resize_image")
        self.assertEqual(log[0]["from_module"], "skills_media")
        self.assertEqual(log[0]["to_module"], "skills_media_convert")  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertTrue(log[0]["reason"])

    def test_loser_after_winner_is_refused(self):
        reg = registry_mod.ToolRegistry()
        reg.register("resize_image", "winner",
                     {"type": "object", "properties": {}},
                     self._stub("skills_media_convert"))  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        with self.assertRaises(ValueError):
            reg.register("resize_image", "loser",
                         {"type": "object", "properties": {}},
                         self._stub("skills_media"), override=True)
        self.assertTrue(reg._tools["resize_image"]["handler"]
                        .__module__.endswith("skills_media_convert"))  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertEqual(reg.override_log(), [])

    def test_wrong_module_pair_is_refused(self):
        reg = registry_mod.ToolRegistry()
        reg.register("screenshot_region", "winner",
                     {"type": "object", "properties": {}},
                     self._stub("skills_screen_capture"))  # Phase C C4 INTENTIONAL: eyes2 renamed to screen_capture
        # skills_media_convert is not the allow-listed winner for  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        # screenshot_region (skills_screen_capture is).
        with self.assertRaises(ValueError):
            reg.register("screenshot_region", "other",
                         {"type": "object", "properties": {}},
                         self._stub("skills_media_convert"), override=True)  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertTrue(reg._tools["screenshot_region"]["handler"]
                        .__module__.endswith("skills_screen_capture"))

    def test_production_override_log(self):
        log = {e["name"]: e for e in registry_mod.tools.override_log()}
        self.assertEqual(set(log), {"resize_image", "convert_image",
                                    "screenshot_region"})
        self.assertEqual(log["resize_image"]["to_module"],
                         "skills_media_convert")  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertEqual(log["convert_image"]["to_module"],
                         "skills_media_convert")  # Phase C C5 INTENTIONAL: media2 renamed to media_convert
        self.assertEqual(log["screenshot_region"]["to_module"],
                         "skills_screen_capture")  # Phase C C4 INTENTIONAL: eyes2 renamed to screen_capture

    def test_literal_duplicate_scan_matches_allowlist(self):
        """Registry-build invariant: no literal duplicate
        registration site exists outside the allow-list."""
        found = {}
        pattern = re.compile(
            r'\.register\(\s*(?:name\s*=\s*)?"([A-Za-z0-9_]+)"')
        for path in sorted((TREE / "core").glob("*.py")):
            text = path.read_text(encoding="utf-8", errors="replace")
            for name in pattern.findall(text):
                found.setdefault(name, []).append(path.name)
        dups = {n for n, files in found.items() if len(files) > 1}
        self.assertEqual(
            dups, set(registry_mod.OVERRIDE_ALLOWLIST),
            f"duplicate sites outside the allow-list: "
            f"{sorted(dups - set(registry_mod.OVERRIDE_ALLOWLIST))}")


# ----------------------------------------------------------------------
# REL-02
# ----------------------------------------------------------------------

class TestRel02Normalisation(unittest.TestCase):
    def test_path_variants_share_a_key(self):
        a = receipts.effect_signatures("file_control",
                                       {"path": "./notes.txt"})
        b = receipts.effect_signatures("file_control",
                                       {"path": "notes.txt"})
        c = receipts.effect_signatures("file_control",
                                       {"path": "notes.txt/"})
        self.assertTrue(set(a) & set(b))
        self.assertTrue(set(a) & set(c))

    def test_url_variants_share_a_key(self):
        base = {"url": "https://example.com/a"}
        for variant in ("https://EXAMPLE.com/a/",
                        "https://example.com:443/a",
                        "https://example.com/a#section"):
            self.assertTrue(
                set(receipts.effect_signatures("ping_host", base))
                & set(receipts.effect_signatures("ping_host",
                                                 {"url": variant})),
                variant)

    def test_recipient_and_host_variants_share_a_key(self):
        self.assertTrue(
            set(receipts.effect_signatures(
                "send_email", {"to": "Alice@Example.com "}))
            & set(receipts.effect_signatures(
                "send_email", {"to": "alice@example.com"})))
        self.assertTrue(
            set(receipts.effect_signatures(
                "ping_host", {"host": "EXAMPLE.com."}))
            & set(receipts.effect_signatures(
                "ping_host", {"host": "example.com"})))

    def test_distinct_effects_stay_distinct(self):
        # Different file, different recipient, different query
        # value, and different query ORDER (order can change
        # meaning, so it is deliberately NOT normalised).
        pairs = [
            ({"path": "a.txt"}, {"path": "b.txt"}),
            ({"to": "alice@example.com"}, {"to": "bob@example.com"}),
            ({"url": "https://example.com/?x=1&y=2"},
             {"url": "https://example.com/?x=1&y=3"}),
            ({"url": "https://example.com/?x=1&y=2"},
             {"url": "https://example.com/?y=2&x=1"}),
        ]
        for first, second in pairs:
            self.assertFalse(
                set(receipts.effect_signatures("probe_tool", first))
                & set(receipts.effect_signatures("probe_tool", second)),
                (first, second))

    def test_guard_matches_normalised_respellings(self):
        store = receipts.ReceiptStore(
            Path(tempfile.mkdtemp()) / "r.jsonl")
        # Regression F1: mark the seed dispatched — an unmarked
        # seed is a call that never dispatched and must not guard.
        _seed = store.record_attempt(tool="file_control",
                                     args={"path": "./notes.txt"})
        self.assertTrue(store.mark_dispatched(_seed["receipt_id"]))
        self.assertIsNotNone(
            store.find_unknown(tool="file_control",
                               args={"path": "notes.txt"}))
        self.assertIsNotNone(
            store.find_unknown(tool="file_control",
                               args={"path": "notes.txt/"}))
        self.assertIsNone(
            store.find_unknown(tool="file_control",
                               args={"path": "other.txt"}))

    def test_old_receipts_guard_exact_only(self):
        """Transition honesty: a pre-Batch-3 receipt (no
        effect_signatures field) guards its exact spelling
        only — normalised guarding needs the new field."""
        tmp = Path(tempfile.mkdtemp()) / "old.jsonl"
        sig = receipts.args_signature("file_control",
                                      {"path": "./notes.txt"})
        tmp.write_text(json.dumps({
            "type": "attempt", "receipt_id": "old1",
            "tool": "file_control", "args_signature": sig,
            "args_preview": "", "created_at": 0.0,
            "status": "attempted", "outcome_summary": ""}) + "\n",
            encoding="utf-8")
        store = receipts.ReceiptStore(tmp)
        self.assertIsNotNone(
            store.find_unknown(tool="file_control",
                               args={"path": "./notes.txt"}))
        self.assertIsNone(
            store.find_unknown(tool="file_control",
                               args={"path": "notes.txt"}))


class TestRel02FunnelAndCooldown(_FunnelBase):
    def test_funnel_refuses_normalised_respell(self):
        # Regression F1: mark the seed dispatched (unknown
        # during dispatch); an unmarked seed must not guard.
        _seed = self.store.record_attempt(tool="b3_probe_write",
                                          args={"path": "./notes.txt"})
        self.assertTrue(
            self.store.mark_dispatched(_seed["receipt_id"]))
        result = self.reg.execute("b3_probe_write",
                                  {"path": "notes.txt"})
        self.assertEqual(self.called, [])
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("unknown outcome", result.get("message", ""))

    def test_funnel_runs_distinct_effect(self):
        self.store.record_attempt(tool="b3_probe_write",
                                  args={"path": "./notes.txt"})
        result = self.reg.execute("b3_probe_write",
                                  {"path": "other.txt"})
        self.assertEqual(result.get("status"), "success")

    def test_cooldown_key_is_normalised(self):
        before = set(handlers_mod._timeout_cooldowns)
        try:
            handlers_mod.note_tool_timeout(
                "b3_probe_write", {"path": "a/b/"})
            self.assertIsNotNone(
                handlers_mod.timeout_cooldown_active(
                    "b3_probe_write", {"path": "a/b"}))
            self.assertIsNone(
                handlers_mod.timeout_cooldown_active(
                    "b3_probe_write", {"path": "a/c"}))
        finally:
            for key in set(handlers_mod._timeout_cooldowns) - before:
                del handlers_mod._timeout_cooldowns[key]


# ----------------------------------------------------------------------
# REL-03 (scoped)
# ----------------------------------------------------------------------

class TestRel03Token(_FunnelBase):
    def test_token_aware_handler_stops_on_cancel(self):
        started = threading.Event()
        stopped = []
        marker = Path(self._tmp.name) / "marker.txt"
        token = cancel_mod.CancellationToken()

        def _handler(cancel_token=None, **kw):
            started.set()
            for _ in range(200):
                if cancel_token is not None and cancel_token.cancelled:
                    stopped.append(True)
                    return {"status": "success", "stopped": True}
                time.sleep(0.02)
            marker.write_text("completed", encoding="utf-8")
            return {"status": "success", "stopped": False}

        self.reg.register(
            "b3_token_probe", "token probe",
            {"type": "object", "properties": {}}, _handler)
        outcome = {}

        def _run():
            outcome["result"] = self.reg.execute(
                "b3_token_probe", {}, cancel_token=token)

        thread = threading.Thread(target=_run)
        thread.start()
        self.assertTrue(started.wait(timeout=10))
        token.cancel()
        thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertEqual(stopped, [True])
        self.assertFalse(marker.exists())
        self.assertEqual(outcome["result"].get("status"), "success")

    def test_token_unaware_handler_gets_no_token(self):
        seen = {}

        def _handler(**kw):
            seen.update(kw)
            return {"ok": True}

        self.reg.register("b3_plain_probe", "plain probe",
                          {"type": "object", "properties": {}},
                          _handler)
        result = self.reg.execute("b3_plain_probe", {})
        self.assertEqual(result.get("status"), "success")
        self.assertNotIn("cancel_token", seen)

    def test_timeout_cancels_token_so_worker_stops_early(self):
        from core.config import config as app_config
        orig = app_config.tool_deadline_standard_s
        app_config.tool_deadline_standard_s = 0.3
        marker = Path(self._tmp.name) / "late_marker.txt"
        saw_cancel = []
        try:
            def _handler(cancel_token=None, **kw):
                for _ in range(200):
                    if cancel_token is not None \
                            and cancel_token.cancelled:
                        saw_cancel.append(True)
                        return {"status": "success",
                                "stopped": True}
                    time.sleep(0.05)
                marker.write_text("completed", encoding="utf-8")
                return {"status": "success", "stopped": False}

            self.reg.register(
                "b3_slow_token_probe", "slow token probe",
                {"type": "object", "properties": {}}, _handler)
            started = time.monotonic()
            result = self.reg.execute("b3_slow_token_probe", {})
            elapsed = time.monotonic() - started
        finally:
            app_config.tool_deadline_standard_s = orig
        self.assertEqual(result.get("status"), "timeout")
        self.assertIn("operation_id", result)
        self.assertLess(elapsed, 5.0)
        time.sleep(1.0)
        self.assertEqual(saw_cancel, [True])
        self.assertFalse(marker.exists())

    def test_late_completion_is_honest_and_pending_listed(self):
        from core.config import config as app_config
        orig = app_config.tool_deadline_standard_s
        app_config.tool_deadline_standard_s = 0.3
        marker = Path(self._tmp.name) / "late.txt"
        try:
            def _handler(**kw):
                time.sleep(0.7)
                marker.write_text("late", encoding="utf-8")
                return {"ok": True}

            self.reg.register(
                "b3_slow_probe", "slow probe",
                {"type": "object", "properties": {}}, _handler)
            result = self.reg.execute("b3_slow_probe", {})
        finally:
            app_config.tool_deadline_standard_s = orig
        self.assertEqual(result.get("status"), "timeout")
        op_id = result.get("operation_id")
        self.assertTrue(op_id)
        # The timeout message says the call may still complete
        # — and the pending list surfaces it before it does.
        pending = ops.list_pending_unknown()
        self.assertIn(op_id, [p["op_id"] for p in pending])
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue(marker.exists(),
                        "abandoned worker never completed (the "
                        "timeout honesty claim would be false)")
        self.assertTrue(ops.is_reconciled(op_id))


class TestRel03Reconciliation(_FunnelBase):
    def _seed_unknown(self, args):
        # Regression F1: the seed is a call that reached dispatch
        # and then went unknown, so it must be marked dispatched
        # for the guard to consult it.
        attempt = self.store.record_attempt(tool="b3_probe_write",
                                            args=args)
        self.assertTrue(
            self.store.mark_dispatched(attempt["receipt_id"]))
        return receipts.args_signature("b3_probe_write", args)

    def test_resolve_happened_keeps_guard_blocking(self):
        sig = self._seed_unknown({"path": "h.txt"})
        op_id = ops.record_timeout("b3_probe_write", "funnel",
                                   args_signature=sig)
        self.assertIn(op_id, [p["op_id"]
                              for p in ops.list_pending_unknown()])
        ok, detail = ops.resolve_pending_unknown(op_id, "happened")
        self.assertTrue(ok)
        self.assertIn("kept blocking", detail)
        self.assertTrue(ops.is_reconciled(op_id))
        self.assertNotIn(op_id, [p["op_id"]
                                 for p in ops.list_pending_unknown()])
        # The effect happened: the guard still refuses a rerun.
        self.assertIsNotNone(
            self.store.find_unknown(tool="b3_probe_write",
                                    args={"path": "h.txt"}))
        ok, _ = ops.resolve_pending_unknown(op_id, "happened")
        self.assertFalse(ok)

    def test_resolve_did_not_happen_releases_guard(self):
        sig = self._seed_unknown({"path": "d.txt"})
        op_id = ops.record_timeout("b3_probe_write", "funnel",
                                   args_signature=sig)
        ok, detail = ops.resolve_pending_unknown(
            op_id, "did_not_happen", note="checked by hand")
        self.assertTrue(ok)
        self.assertIn("released", detail)
        self.assertTrue(ops.is_reconciled(op_id))
        self.assertIsNone(
            self.store.find_unknown(tool="b3_probe_write",
                                    args={"path": "d.txt"}))
        # And the funnel now lets the verified-absent call run.
        result = self.reg.execute("b3_probe_write", {"path": "d.txt"})
        self.assertEqual(result.get("status"), "success")

    def test_resolve_errors_are_honest(self):
        ok, _ = ops.resolve_pending_unknown("no-such-id", "happened")
        self.assertFalse(ok)
        op_id = ops.record_timeout("b3_probe_write", "funnel")
        ok, detail = ops.resolve_pending_unknown(op_id, "maybe")
        self.assertFalse(ok)
        ok, detail = ops.resolve_pending_unknown(
            op_id, "did_not_happen")
        self.assertTrue(ok)
        self.assertIn("unavailable", detail)

    def test_unbounded_set_is_pinned(self):
        none_tools = {name for name, cls
                      in handlers_mod._TOOL_DEADLINE_CLASS.items()
                      if cls == "none"}
        # Both earn "none" in writing: their duration IS the
        # user's request (a recording that lasts as asked).
        # Any addition here is a visible decision, not a drift.
        self.assertEqual(none_tools, {"record_audio", "voice_note"})
        for name in none_tools:
            self.assertIsNone(handlers_mod.deadline_for(name))
        self.assertIsNotNone(
            handlers_mod.deadline_for("type_text"))


# ----------------------------------------------------------------------
# CAP-01
# ----------------------------------------------------------------------

class TestCap01Kind(unittest.TestCase):
    def test_marked_set_is_pinned(self):
        self.assertEqual(registry_mod.TOOL_KIND_TABLE, CAP01_EXPECTED)
        decls = {d["name"]: d for d in
                 registry_mod.tools.get_declarations()}
        marked = {n: d["kind"] for n, d in decls.items()
                  if "kind" in d}
        self.assertEqual(marked, CAP01_EXPECTED)
        # Nothing else carries a kind field.
        self.assertEqual(set(marked), set(CAP01_EXPECTED))

    def test_descriptor_probes_carry_false_flags(self):
        from core.iotl_tools import iot_local_device_handler
        from core.pilot_tools import pilot_dev_workflow_handler
        from core.travel_tools import travel_trip_handler
        trip = json.loads(travel_trip_handler(
            legs=[{"origin": "A", "destination": "B"}]))
        self.assertFalse(trip["computed"])
        self.assertFalse(trip["booked"])
        self.assertFalse(trip["routed"])
        self.assertFalse(trip["paid"])
        device = json.loads(iot_local_device_handler(
            device_name="lamp", address_label="192.168.1.5",
            network_label="home"))
        self.assertFalse(device["reached"])
        self.assertFalse(device["controlled"])
        workflow = json.loads(pilot_dev_workflow_handler(
            repo_label="demo", steps=["build", "test"]))
        self.assertFalse(workflow["executed"])
        self.assertFalse(workflow["deployed"])
        self.assertFalse(workflow["signed"])

    def test_kind_values_are_a_closed_vocabulary(self):
        for kind in registry_mod.TOOL_KIND_TABLE.values():
            self.assertIn(kind, registry_mod.TOOL_KIND_VALUES)
        with self.assertRaises(ValueError):
            registry_mod.ToolRegistry().register(
                "b3_kind_probe", "probe",
                {"type": "object", "properties": {}},
                lambda **kw: {"ok": True}, kind="operational-x")


# ----------------------------------------------------------------------
# COV-02 (the 5 only)
# ----------------------------------------------------------------------

class TestCov02Five(unittest.TestCase):
    def test_five_are_out_of_read_only(self):
        for name in COV02_FIVE:
            with self.subTest(tool=name):
                self.assertNotIn(name, gate._READ_ONLY_TOOLS)
                self.assertEqual(gate._FREE_BY_DESIGN.get(name),
                                 "effectful read")
                self.assertFalse(gate._is_read_class(name, {}))

    def test_five_still_gate_under_taint(self):
        orig_tainted = injection.tainted
        orig_kind = injection.taint_kind
        try:
            injection.tainted = lambda: True
            injection.taint_kind = lambda: "hard"
            for name in COV02_FIVE:
                with self.subTest(tool=name):
                    self.assertTrue(
                        gate._needs_confirmation(name, {}))
            injection.taint_kind = lambda: "soft"
            for name in COV02_FIVE:
                with self.subTest(tool=name):
                    if name == "browser_read":
                        # Click-anywhere Batch 0 (Plan 9 §4, N-13):
                        # browser_read is Tier 0 — it passes under
                        # soft taint (its content is wrapped +
                        # screened at the producer) and gates only
                        # under hard taint (pinned in the hard half
                        # above). The other four stay Tier 2 + gate.
                        self.assertEqual(gate._tool_tier(name, {}), 0)
                        self.assertFalse(
                            gate._needs_confirmation(name, {}))
                        continue
                    self.assertEqual(gate._tool_tier(name, {}), 2)
                    self.assertTrue(
                        gate._needs_confirmation(name, {}))
        finally:
            injection.tainted = orig_tainted
            injection.taint_kind = orig_kind
            injection.clear_taint(force=True)

    def test_five_stay_free_untainted(self):
        # Deliberate free-by-design: reclassification adds
        # failsafe/provenance/receipt gating, NOT a popup on
        # every untainted probe or page read.
        injection.clear_taint(force=True)
        for name in COV02_FIVE:
            with self.subTest(tool=name):
                self.assertFalse(gate._needs_confirmation(name, {}))

    def test_other_17_still_read_only(self):
        for name in COV02_OTHER_17:
            with self.subTest(tool=name):
                self.assertIn(name, gate._READ_ONLY_TOOLS)
                self.assertTrue(gate._is_read_class(name, {}))


class TestCov02Funnel(_FunnelBase):
    def test_reclassified_tool_refuses_under_failsafe(self):
        self.reg.register(
            "ping_host", "batch3 stub probe",
            {"type": "object",
             "properties": {"host": {"type": "string"}}},
            lambda **kw: {"ok": True})
        failsafe.tripped = lambda: True
        result = self.reg.execute("ping_host", {"host": "example.com"})
        self.assertEqual(result.get("status"), "refused")
        # Reads stay exempt under the same trip.
        result = self.reg.execute("read_file", {"path": "a.txt"})
        self.assertEqual(result.get("status"), "success")

    def test_reclassified_tool_now_carries_receipt_guard(self):
        self.reg.register(
            "ping_host", "batch3 stub probe",
            {"type": "object",
             "properties": {"host": {"type": "string"}}},
            lambda **kw: {"ok": True})
        # Regression F1: the guard consults dispatched attempts
        # only; mark the seed as dispatched (unknown during
        # dispatch), or the seed is a never-dispatched call and
        # correctly does not guard.
        _seed = self.store.record_attempt(tool="ping_host",
                                          args={"host": "example.com"})
        self.assertTrue(
            self.store.mark_dispatched(_seed["receipt_id"]))
        result = self.reg.execute("ping_host", {"host": "example.com"})
        self.assertEqual(result.get("status"), "refused")
        self.assertIn("unknown outcome", result.get("message", ""))


# ----------------------------------------------------------------------
# REL-04
# ----------------------------------------------------------------------

class TestRel04TieredFsync(unittest.TestCase):
    def test_tier_is_small_named_and_gated(self):
        tier = receipts.CRITICAL_RECEIPT_TIER
        self.assertLessEqual(len(tier), 20)
        self.assertTrue(tier)
        self.assertTrue(tier <= set(gate._CONFIRM_GATE_TOOLS))
        self.assertTrue(
            tier <= set(registry_mod.tools._tools),
            "tier names a tool that is not registered")

    def test_fsync_only_for_critical_tier(self):
        store = receipts.ReceiptStore(
            Path(tempfile.mkdtemp()) / "r.jsonl")
        calls = []
        orig_fsync = os.fsync

        def _counting(fd):
            calls.append(fd)
            return orig_fsync(fd)

        with mock.patch.object(os, "fsync", _counting):
            attempt = store.record_attempt(tool="send_email",
                                           args={"to": "a@b.test"})
            store.record_outcome(attempt["receipt_id"],
                                 status="succeeded")
            critical_calls = len(calls)
            self.assertGreaterEqual(critical_calls, 2)
            attempt = store.record_attempt(tool="read_file",
                                           args={"path": "a.txt"})
            store.record_outcome(attempt["receipt_id"],
                                 status="succeeded")
            self.assertEqual(len(calls), critical_calls)


if __name__ == "__main__":
    unittest.main()
