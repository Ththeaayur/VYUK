"""GAPS-13 suite: the remaining flaw-#10 fail-open pockets are closed.

Mirrors prove_failopen_pockets.py's live checks as unit tests.
No real handler side effect: every check stubs its tool's handler,
and every patch (confirm gate, staleness verdict, blocked import,
live-origin token, taint, instruction, dictation flag) is restored.

Determinism notes: setUp resets the Rule-of-Two session budget,
clears session taint and any published instruction, and resets the
dictation flag, so earlier suites in the same process cannot leak
state into these checks. The staleness tests set the live-origin
marker only for their own call and reset it immediately after.
The import blocks delete the submodule's attribute from the core
package as well as setting sys.modules to None — `from .. import X`
uses the already-bound attribute directly, so None alone does not
block it (found while proving the gap).
"""

import json
import os
import sys
import unittest

import core.confirm_gate as confirm_gate
import core.injection as injection
from core import session_epoch as real_epoch
from core.brain import turn_context
from core.desktop import live_staleness as ls_mod
from core.funnel import live_origin, rule_of_two as rot_mod
from core.funnel.tools_gate import (
    _needs_confirmation, curl_cffi_lane_needs_confirmation)
from core.funnel.tools_registry import tools

TREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _block_import(modname):
    """Block `from core import <modname>` for real; see the module
    docstring. Returns a restore closure."""
    import core as _core_pkg
    attr = modname.split(".")[-1]
    saved_mod = sys.modules.get(modname)
    saved_attr = getattr(_core_pkg, attr, None)
    had_attr = hasattr(_core_pkg, attr)
    sys.modules[modname] = None
    if had_attr:
        delattr(_core_pkg, attr)

    def _restore():
        if saved_mod is None:
            sys.modules.pop(modname, None)
        else:
            sys.modules[modname] = saved_mod
        if had_attr:
            setattr(_core_pkg, attr, saved_attr)
        elif hasattr(_core_pkg, attr):
            delattr(_core_pkg, attr)

    return _restore


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig_has_handler = confirm_gate.has_handler
        self._orig_request = confirm_gate.request_confirmation
        self._orig_check = ls_mod.check_staleness
        try:
            injection.clear_taint(force=True)
        except Exception:  # noqa: BLE001 — determinism best-effort
            pass
        turn_context.clear_current_instruction()
        turn_context.set_dictation_active(False)
        rot_mod.reset_for_tests()

    def tearDown(self):
        confirm_gate.has_handler = self._orig_has_handler
        confirm_gate.request_confirmation = self._orig_request
        ls_mod.check_staleness = self._orig_check
        turn_context.clear_current_instruction()
        turn_context.set_dictation_active(False)
        try:
            injection.clear_taint(force=True)
        except Exception:  # noqa: BLE001 — determinism best-effort
            pass
        rot_mod.reset_for_tests()

    def _run(self, tool, args, handler_name):
        ran = []
        orig = tools._tools[handler_name]["handler"]

        def _stub(**kw):
            ran.append(True)
            return "stubbed-ok"

        tools._tools[handler_name]["handler"] = _stub
        try:
            result = tools.execute(tool, args)
        finally:
            tools._tools[handler_name]["handler"] = orig
        return result, ran

    def _click(self):
        return self._run("mouse_control",
                         {"action": "click", "x": 100, "y": 100},
                         "mouse_control")

    def _type(self, text):
        return self._run("type_text", {"text": text}, "type_text")

    def _window_close(self, bump):
        ran = []
        orig = tools._tools["window_control"]["handler"]

        def _stub(**kw):
            ran.append(True)
            return "stubbed-ok"

        tools._tools["window_control"]["handler"] = _stub
        confirm_gate.has_handler = lambda: True

        def _ask(*a, **k):
            if bump:
                real_epoch.bump_epoch("test-failopen")
            return True

        confirm_gate.request_confirmation = _ask
        try:
            result = tools.execute("window_control",
                                   {"action": "close"})
        finally:
            confirm_gate.request_confirmation = self._orig_request
            confirm_gate.has_handler = self._orig_has_handler
            tools._tools["window_control"]["handler"] = orig
        return result, ran


class TestLiveStalenessFailClosed(_Base):
    def test_unchecked_no_baseline_refuses(self):
        token = live_origin.set_live_origin()
        ls_mod.clear_delivery_ledger()
        try:
            result, ran = self._click()
        finally:
            live_origin.reset_live_origin(token)
        self.assertFalse(ran)
        self.assertEqual(result.get("kind"), "stale-view")

    def test_check_raised_refuses(self):
        token = live_origin.set_live_origin()

        def _boom(*a, **k):
            raise RuntimeError("boom")

        ls_mod.check_staleness = _boom
        try:
            result, ran = self._click()
        finally:
            live_origin.reset_live_origin(token)
        self.assertFalse(ran)
        self.assertEqual(result.get("kind"), "stale-view")

    def test_fresh_verdict_proceeds(self):
        token = live_origin.set_live_origin()
        ls_mod.check_staleness = lambda *a, **k: {"verdict": "fresh"}
        try:
            result, ran = self._click()
        finally:
            live_origin.reset_live_origin(token)
        self.assertTrue(ran)
        self.assertEqual(result.get("status"), "success")

    def test_stale_verdict_refuses(self):
        token = live_origin.set_live_origin()
        ls_mod.check_staleness = lambda *a, **k: {
            "verdict": "stale", "mean_abs_diff": 12.5,
            "frac_large": 0.3}
        try:
            result, ran = self._click()
        finally:
            live_origin.reset_live_origin(token)
        self.assertFalse(ran)
        self.assertEqual(result.get("kind"), "stale-view")


class TestEchoGuardFailClosed(_Base):
    def test_echo_refused_when_importable(self):
        turn_context.set_current_instruction("find flaws in every tool")
        result, ran = self._type("(find flaws in every tool)")
        self.assertFalse(ran)
        self.assertEqual(result.get("kind"), "refused")
        self.assertIn("just repeats the user's own instruction",
                      result.get("message", ""))

    def test_echo_refused_when_unimportable(self):
        turn_context.set_current_instruction("find flaws in every tool")
        restore = _block_import("core.echo_guard")
        try:
            result, ran = self._type("(find flaws in every tool)")
        finally:
            restore()
        self.assertFalse(ran)
        self.assertEqual(result.get("kind"), "refused")
        self.assertIn("could not be consulted",
                      result.get("message", ""))

    def test_benign_payload_proceeds(self):
        turn_context.set_current_instruction("find flaws in every tool")
        result, ran = self._type("The report is ready for review.")
        self.assertTrue(ran)
        self.assertEqual(result.get("status"), "success")

    def test_dictation_carveout_proceeds(self):
        turn_context.set_current_instruction("find flaws in every tool")
        turn_context.set_dictation_active(True)
        result, ran = self._type("(find flaws in every tool)")
        self.assertTrue(ran)
        self.assertEqual(result.get("status"), "success")

    def test_no_instruction_carveout_proceeds(self):
        result, ran = self._type("(find flaws in every tool)")
        self.assertTrue(ran)
        self.assertEqual(result.get("status"), "success")


class TestEpochFailClosed(_Base):
    def test_bumped_refuses_when_present(self):
        result, ran = self._window_close(bump=True)
        self.assertFalse(ran)
        self.assertEqual(result.get("kind"), "refused")

    def test_bumped_refuses_when_unimportable(self):
        restore = _block_import("core.session_epoch")
        try:
            result, ran = self._window_close(bump=True)
        finally:
            restore()
        self.assertFalse(ran)
        self.assertEqual(result.get("kind"), "refused")

    def test_unmoved_proceeds(self):
        result, ran = self._window_close(bump=False)
        self.assertTrue(ran)
        self.assertEqual(result.get("status"), "success")


class TestGateClassifications(_Base):
    def test_three_predicates_exist(self):
        funnel = os.path.join(TREE, "core", "funnel")
        gate_src = _read(os.path.join(funnel, "tools_gate.py"))
        netlane_src = _read(os.path.join(
            funnel, "tools_gate_netlane.py"))
        self.assertIn("def _needs_confirmation(", gate_src)
        self.assertIn("def mcp_needs_confirmation(", gate_src)
        self.assertIn("def curl_cffi_lane_needs_confirmation(",
                      netlane_src)

    def test_lane_only_reachable_from_funnel_gated_handler(self):
        funnel = os.path.join(TREE, "core", "funnel")
        handlers_src = _read(os.path.join(
            funnel, "tools_handlers_data_services.py"))
        self.assertGreaterEqual(
            handlers_src.count("curl_cffi_read_tool("), 1)

    def test_same_predicate_consumers(self):
        plan_src = _read(os.path.join(
            TREE, "core", "plan_approval.py"))
        trials_src = _read(os.path.join(TREE, "core", "mcp_trials.py"))
        self.assertIn("_needs_confirmation(", plan_src)
        self.assertIn("mcp_needs_confirmation(", trials_src)

    def test_soft_taint_disagreement_resolves_stricter(self):
        injection.note_untrusted(
            "web page",
            "A quiet recipe for lentil soup with cumin and lemon.")
        try:
            self.assertEqual(injection.taint_kind(), "soft")
            self.assertIs(_needs_confirmation("screen_look", {}), False)
            self.assertIs(
                curl_cffi_lane_needs_confirmation(True), True)
        finally:
            injection.clear_taint(force=True)


class TestCensusUnchanged(_Base):
    def test_census(self):
        decls = tools.get_declarations()
        names = [d["name"] for d in decls]
        self.assertEqual(len(decls), 764)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(len(set(names)), 764)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        params = sum(len(d["parameters"]["properties"]) for d in decls)
        self.assertEqual(params, 2102)  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 763)  # INTENTIONAL — Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 761)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 169)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        audit = _read(os.path.join(TREE, "TOOLS_LOOPHOLES_AUDIT.md"))
        for fragment in ('"total": 764', '"read": 384',  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
                         '"effectful": 380', '"confirm_gated": 169',  # INTENTIONAL — Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374/164 -> 375/165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
                         '"already_verified_in_handler": 314'):  # INTENTIONAL — Click-anywhere (2026-11-14): 306 -> 307 — click_anywhere verified in handler; field-check fix #32 (2026-11-18): 307 -> 308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 308 -> 309 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
            self.assertIn(fragment, audit)
        snap = json.loads(open(os.path.join(
            TREE, "tests", "_classification_snapshot.json"),
            encoding="utf-8").read())
        self.assertEqual(len(snap), 764)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(
            sum(1 for v in snap.values() if v.get("read")), 384)  # INTENTIONAL — round-3 fix Q6 (2026-11-19): 382 -> 383 — one new read tool get_morning_briefing; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(
            sum(1 for v in snap.values() if not v.get("read")), 380)  # INTENTIONAL — Click-anywhere (2026-11-14): 372 -> 373 — click_anywhere is effectful; field-check fix #32 (2026-11-18): 373 -> 374 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 374 -> 375 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(
            sum(1 for v in snap.values() if v.get("confirm")), 169)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan (confirm-gated); fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169


if __name__ == "__main__":
    unittest.main()
