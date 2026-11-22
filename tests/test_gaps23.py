"""GAPS-23: the section 6.3 F-series (tool registry + funnel).

F-V1 — companion server is a funnel bypass: already closed by GAPS-9
(_funnel_call, fail-closed 503, no direct-call fallback); pinned here.
F-V2 — MCP branch is a second, divergent funnel: already closed by
GAPS-12+13 (_execute_mcp deleted; MCP runs the native stages); pinned.
F-V3 — one gate classification: core/funnel/tool_classification.py
is the single source; the three set modules are pure re-exports;
_needs_confirmation and _is_read_class derive from classify_call.
The stage_lighting/wardrobe read branches are narrowed to the
actions whose handlers actually dispatch on mode (a DMX send or a
wear with mode=list is NOT a read).
F-V4 — execute() stage order is enforced: EXECUTE_STAGES (23 stages)
+ one loop; the exact order is pinned.
F-V5 — register() seam: the allow-list/kind/schema tables live in
core/funnel/tools_registry_core.py; the facade re-exports them.
F-V6 — one approval record shape: every decision names its authority
(popup / allowlist / batched / no-handler / timeout / money-guard);
the audit value names an allowlisted approval "allowlist-approved".
F-V7 — capability boundary: direct handler calls (mixin methods,
module-level functions, stored handlers) refuse outside the funnel;
one catalogue source (core/catalogue_groups_data.py, GUI shim
re-exports it); the registry checks catalogue agreement at import.
F-V8 — declarative registry: PACKS (92 packs, one loop, order pinned)
+ frozen ToolSpec + register_spec().
"""
import ast
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TREE))

from core import confirm_gate  # noqa: E402
from core.funnel.tool_classification import classify_call  # noqa: E402
from core.funnel.tools_registry import PACKS, ToolRegistry, tools  # noqa: E402
from core.funnel.tools_registry_core import ToolSpec  # noqa: E402

EXECUTE_STAGES = ("_exec_setup", "_exec_receipt", "_exec_mark_cb",
                  "_exec_permission", "_exec_folder_scope", "_exec_route",
                  "_exec_toolbox", "_exec_live_staleness", "_exec_validate",
                  "_exec_failsafe", "_exec_killchain", "_exec_protected_path",
                  "_exec_protected_dest", "_exec_install", "_exec_echo",
                  "_exec_confirm", "_exec_epoch_recheck", "_exec_rule_of_two",
                  "_exec_limits", "_exec_receipt_check",
                  "_exec_mark_dispatched", "_exec_deadline_legs",
                  "_exec_dispatch")

PACKS_EXPECTED = (
    'extended', 'control', 'mind', 'episode', 'complete', 'cursor',
    'uia', 'browser', 'world', 'skills', 'mega', 'meeting', 'summary',
    'browser_cdp_inspect', 'browser_history', 'browser_adblock',
    'article_extract', 'ollama_models', 'event_planning',
    'event_ticketing', 'technical_analysis', 'time_tracking',
    'eye_rest', 'wormhole', 'status_strip', 'basic_pitch',
    'discord_presence', 'rustic_backup', 'companion', 'wake_training',
    'dagflow', 'proactivity', 'hands2', 'hands3', 'hands4', 'hands5',
    'play', 'create2', 'nose', 'hands6', 's4', 'wave2', 'wave3',
    'toolbox', 'wave6', 'conv_w45', 'study_t1', 'triage2',
    'audio_localise', 'optimise', 'erp', 'media_chat', 'invoice',
    'translations', 'filament', 'deals', 'vehicle_routes',
    'social_send', 'click_anywhere', 'scheduled_agents', 'triage3',
    'triage4', 'wave6_integration', 'sandbox_w7', 'undo_w7', 'sim_w7',
    'proposals_w7', 'multiverse_w7', 'antic_w8', 'release_w8',
    'phase5_w8', 'feel_w8', 'learnlast_w8', 'firstopen_w9', 'well_w9',
    'comms_w9', 'iotl_w9', 'hardware_w9', 'access_w9', 'net_w9',
    'gaming_w9', 'pilot_w9', 'travel_w9', 'grad_w9', 'release_w10',
    'trust_w10', 'firstrun_w10', 'accessconf_w10', 'libwire',
    's7consumers', 's7consumers2', 's7consumers3')


def _last_authority():
    decision = confirm_gate.last_decision()
    return decision.get("authority") if decision else None


class Fv1Fv2ClosureTests(unittest.TestCase):
    def test_companion_execute_only_inside_funnel_call(self):
        src = (TREE / "core" / "companion.py").read_text(encoding="utf-8")
        lines = src.splitlines()
        hits = [i + 1 for i, ln in enumerate(lines)
                if "tools.execute" in ln]
        self.assertEqual(len(hits), 1)
        start = next(i + 1 for i, ln in enumerate(lines)
                     if ln.startswith("def _funnel_call("))
        end = next((i + 1 for i in range(start, len(lines))
                    if lines[i].startswith("def ")
                    or lines[i].startswith("class ")), len(lines))
        self.assertTrue(start <= hits[0] <= end)
        body = "\n".join(lines[start - 1:end])
        self.assertIn("except", body)
        self.assertIn("503", body)

    def test_companion_503s_when_registry_import_breaks(self):
        import core.companion as comp_mod
        real = sys.modules.get("core.funnel.tools_registry")
        sys.modules["core.funnel.tools_registry"] = None
        try:
            _, status = comp_mod._funnel_call("test_tool", {})
        finally:
            if real is None:
                sys.modules.pop("core.funnel.tools_registry", None)
            else:
                sys.modules["core.funnel.tools_registry"] = real
        self.assertEqual(status, 503)

    def test_no_execute_mcp_definition_in_core(self):
        defs = []
        for path in (TREE / "core").rglob("*.py"):
            if "__pycache__" in str(path):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and node.name == "_execute_mcp":
                    defs.append(str(path.relative_to(TREE)))
        self.assertEqual(defs, [])

    def test_mcp_confirm_shares_native_ask_path(self):
        gates = (TREE / "core" / "funnel"
                 / "tools_registry_exec_gates.py").read_text(
                     encoding="utf-8")
        self.assertIn("def _exec_confirm_mcp", gates)
        start = gates.index("def _exec_confirm_mcp")
        rest = gates[start:]
        end = rest.index("\n    def ", 1)
        body = rest[:end]
        self.assertTrue("request_confirmation" in body
                        or "_ask_human" in body)


class Fv3ClassificationTests(unittest.TestCase):
    def test_three_sets_partition_registry(self):
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        from core.funnel.tools_gate_readonly import _READ_ONLY_TOOLS
        from core.funnel.tools_gate_free import _FREE_BY_DESIGN
        names = {d["name"] for d in tools.get_declarations()}
        free = set(_FREE_BY_DESIGN)
        self.assertEqual(
            len(_CONFIRM_GATE_TOOLS) + len(_READ_ONLY_TOOLS) + len(free),
            len(names))
        self.assertFalse(_CONFIRM_GATE_TOOLS & _READ_ONLY_TOOLS)
        self.assertFalse(_CONFIRM_GATE_TOOLS & free)
        self.assertFalse(_READ_ONLY_TOOLS & free)
        self.assertEqual(
            names - (_CONFIRM_GATE_TOOLS | _READ_ONLY_TOOLS | free), set())

    def test_three_default_call_tools_are_read_not_confirm(self):
        for name in ("power_plans", "task_scheduler_control",
                     "vpn_control"):
            self.assertEqual(classify_call(name, {}), (False, True))

    def test_invariant_never_needs_and_read(self):
        names = [d["name"] for d in tools.get_declarations()]
        actions = ["", "list", "get", "status", "set", "run", "enable",
                   "disable", "connect", "disconnect", "close", "add",
                   "remove", "create", "delete", "start", "stop"]
        violations = []
        for name in names:
            for action in actions:
                cc = classify_call(name, {"action": action})
                if cc.needs_confirmation and cc.is_read:
                    violations.append((name, action))
            for action, mode in (("customer", "list"), ("scene", "list"),
                                 ("server", "list"), ("garment", "list"),
                                 ("output", "list")):
                cc = classify_call(name, {"action": action, "mode": mode})
                if cc.needs_confirmation and cc.is_read:
                    violations.append((name, (action, mode)))
        self.assertEqual(violations, [])

    def test_read_branch_narrowing(self):
        # A DMX send / a wear with mode=list is NOT a read, even
        # though the mode is "list"; the list actions themselves are.
        self.assertIs(
            classify_call("wardrobe",
                          {"action": "wear", "mode": "list"}).is_read,
            False)
        self.assertIs(
            classify_call("wardrobe",
                          {"action": "garment", "mode": "list"}).is_read,
            True)
        self.assertIs(
            classify_call("stage_lighting",
                          {"action": "output", "mode": "list"}).is_read,
            False)
        self.assertIs(
            classify_call("stage_lighting",
                          {"action": "scene", "mode": "list"}).is_read,
            True)


class Fv4StageOrderTests(unittest.TestCase):
    def test_execute_stages_exact_order(self):
        from core.funnel import tools_registry as reg_mod
        self.assertEqual(reg_mod.EXECUTE_STAGES, EXECUTE_STAGES)
        self.assertEqual(len(EXECUTE_STAGES), 23)

    def test_execute_is_a_loop(self):
        src = (TREE / "core" / "funnel" / "tools_registry.py").read_text(
            encoding="utf-8")
        self.assertEqual(
            len(re.findall(r"result = self\._exec_\w+\(ctx\)", src)), 0)
        self.assertIn("for stage in EXECUTE_STAGES", src)

    def test_execute_read_succeeds_unknown_is_unavailable(self):
        ok = tools.execute("get_current_time_date", {})
        self.assertEqual(ok.get("status"), "success")
        bad = tools.execute("no_such_tool_gaps23", {})
        self.assertEqual(bad.get("status"), "failure")
        self.assertEqual(bad.get("kind"), "unavailable")


class Fv5RegisterSeamTests(unittest.TestCase):
    def test_no_local_import_in_register(self):
        src = (TREE / "core" / "funnel"
               / "tools_registry_core.py").read_text(encoding="utf-8")
        start = src.index("def register(")
        rest = src[start:]
        end = rest.index("\n    def ", 1)
        body = rest[:end]
        self.assertNotIn("from . import tools_registry", body)
        self.assertNotIn("from .tools_registry import", body)

    def test_tables_defined_in_core_module(self):
        src = (TREE / "core" / "funnel"
               / "tools_registry_core.py").read_text(encoding="utf-8")
        for pattern in (r"^OPEN_SCHEMA_TOOLS\s*=",
                        r"^OVERRIDE_ALLOWLIST\s*[:=]",
                        r"^TOOL_KIND_TABLE\s*[:=]",
                        r"^TOOL_KIND_VALUES\s*="):
            self.assertIsNotNone(re.search(pattern, src, re.M), pattern)

    def test_facade_reexports_are_core_objects(self):
        from core.funnel import tools_registry as reg_mod
        from core.funnel import tools_registry_core as core_mod
        self.assertIs(reg_mod.OVERRIDE_ALLOWLIST,
                      core_mod.OVERRIDE_ALLOWLIST)
        self.assertIs(reg_mod.TOOL_KIND_TABLE, core_mod.TOOL_KIND_TABLE)
        self.assertIs(reg_mod.OPEN_SCHEMA_TOOLS, core_mod.OPEN_SCHEMA_TOOLS)


class Fv6AuthorityTests(unittest.TestCase):
    def setUp(self):
        self._allow_tmp = tempfile.NamedTemporaryFile(
            prefix="gaps23_allow_", suffix=".json", delete=False)
        self._allow_tmp.close()
        import core.confirm_gate_allowlist as allow_mod
        self._allow_mod = allow_mod
        self._old_file = allow_mod._ALLOWLIST_FILE
        allow_mod._ALLOWLIST_FILE = self._allow_tmp.name

    def tearDown(self):
        confirm_gate.clear_handler()
        self._allow_mod._ALLOWLIST_FILE = self._old_file
        try:
            os.unlink(self._allow_tmp.name)
        except OSError:
            pass

    def test_allowlisted_ask_records_allowlist(self):
        self.assertTrue(self._allow_mod.allow_kind("mouse_control"))
        confirm_gate.clear_handler()
        self.assertTrue(confirm_gate.request_confirmation(
            "mouse_control", {}))
        self.assertEqual(_last_authority(), "allowlist")

    def test_popup_approve_and_decline_record_popup(self):
        confirm_gate.register_handler(lambda *a: True)
        self.assertTrue(confirm_gate.request_confirmation(
            "mouse_control", {}))
        self.assertEqual(_last_authority(), "popup")
        confirm_gate.register_handler(lambda *a: False)
        self.assertFalse(confirm_gate.request_confirmation(
            "mouse_control", {}))
        self.assertEqual(_last_authority(), "popup")

    def test_no_handler_records_no_handler(self):
        confirm_gate.clear_handler()
        self.assertFalse(confirm_gate.request_confirmation(
            "mouse_control", {}))
        self.assertEqual(_last_authority(), "no-handler")

    def test_money_guard_records_money_guard(self):
        confirm_gate.register_handler(lambda *a: True)
        self.assertFalse(confirm_gate.request_confirmation(
            "payment_test", {}))
        self.assertEqual(_last_authority(), "money-guard")

    def test_timeout_records_timeout(self):
        def _never(*a):
            time.sleep(30)

        confirm_gate.register_handler(_never)
        self.assertFalse(confirm_gate.request_confirmation(
            "mouse_control", {}, timeout_s=0.2))
        self.assertEqual(_last_authority(), "timeout")

    def test_audit_value_names_allowlist_approval(self):
        import core.audit_chain as audit_chain
        events = []
        orig_append = audit_chain.append

        def _spy(kind, data, **kw):
            events.append((kind, dict(data)))
            return orig_append(kind, data, **kw)

        # GAPS-27 (M-V2): the funnel's per-call audit record
        # is written exactly once, as the FULL record into
        # the shared chain via audit.write_record ->
        # audit_chain.append("audit", record). The old spy
        # target (record_family.append_event("audit", ...))
        # was the funnel's second, subset write — removed
        # by the merge — so the spy observes the chain
        # append now; the gate it asserts is unchanged.
        audit_chain.append = _spy
        orig_ask = confirm_gate.request_confirmation
        confirm_gate.register_handler(lambda *a: True)
        probe = Path(tempfile.gettempdir()) / "gaps23_suite_v6h.txt"
        if probe.exists():
            probe.unlink()
        try:
            def _allow_stub(name, args=None, **kw):
                confirm_gate._log_decision(
                    name, True, "stubbed allowlist",
                    request_id=confirm_gate._next_request_id(),
                    authority="allowlist")
                return True

            confirm_gate.request_confirmation = _allow_stub
            result = tools.execute("write_file", {
                "filepath": str(probe), "content": "gaps23 suite v6h"})
            gates = [p.get("gate") for k, p in events
                     if k == "audit" and p.get("tool") == "write_file"]
            self.assertEqual(result.get("status"), "success")
            self.assertTrue(probe.exists())
            self.assertIn("allowlist-approved", gates)

            def _popup_stub(name, args=None, **kw):
                return True  # mocked ask: logs nothing, joins nothing

            confirm_gate.request_confirmation = _popup_stub
            events.clear()
            if probe.exists():
                probe.unlink()
            result = tools.execute("write_file", {
                "filepath": str(probe), "content": "gaps23 suite v6i"})
            gates = [p.get("gate") for k, p in events
                     if k == "audit" and p.get("tool") == "write_file"]
            self.assertEqual(result.get("status"), "success")
            self.assertTrue(probe.exists())
            self.assertIn("user-approved", gates)
        finally:
            confirm_gate.request_confirmation = orig_ask
            audit_chain.append = orig_append
            confirm_gate.clear_handler()
            if probe.exists():
                probe.unlink()


class Fv7BoundaryTests(unittest.TestCase):
    def test_bare_mixin_direct_call_refused(self):
        from core.funnel.tools_handlers_data_services import (
            ToolHandlersDataServicesMixin)
        probe = Path(tempfile.gettempdir()) / "gaps23_suite_v7a.txt"
        if probe.exists():
            probe.unlink()
        mixin = object.__new__(ToolHandlersDataServicesMixin)
        with self.assertRaises(RuntimeError) as ctx:
            mixin._tool_write_file(str(probe), "gaps23 suite v7a")
        self.assertIn("funnel", str(ctx.exception).lower())
        self.assertFalse(probe.exists())

    def test_module_level_direct_call_refused(self):
        import core.meeting as meeting_mod
        with self.assertRaises(RuntimeError) as ctx:
            meeting_mod._tool_meeting_status({})
        self.assertIn("funnel", str(ctx.exception).lower())

    def test_stored_handler_refuses_direct_call(self):
        probe = Path(tempfile.gettempdir()) / "gaps23_suite_v7c.txt"
        if probe.exists():
            probe.unlink()
        stored = tools._tools["write_file"]["handler"]
        with self.assertRaises(RuntimeError) as ctx:
            stored({"filepath": str(probe), "content": "gaps23 v7c"})
        self.assertIn("funnel", str(ctx.exception).lower())
        self.assertFalse(probe.exists())

    def test_funnel_execute_still_writes(self):
        confirm_gate.register_handler(lambda *a: True)
        probe = Path(tempfile.gettempdir()) / "gaps23_suite_v7e.txt"
        if probe.exists():
            probe.unlink()
        try:
            result = tools.execute("write_file", {
                "filepath": str(probe), "content": "gaps23 suite v7e"})
            self.assertEqual(result.get("status"), "success")
            self.assertTrue(probe.exists())
        finally:
            confirm_gate.clear_handler()
            if probe.exists():
                probe.unlink()


class Fv7CatalogueTests(unittest.TestCase):
    def test_single_source_group_counts(self):
        from core.catalogue_groups_data import GROUP_MEMBERS, GROUP_ORDER
        self.assertEqual(len(GROUP_ORDER), 11)
        self.assertEqual(sum(len(v) for v in GROUP_MEMBERS.values()), 761)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 755 -> 759 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        # Import-time invariant: no tool in two groups.
        seen = {}
        for group, names in GROUP_MEMBERS.items():
            for name in names:
                self.assertNotIn(name, seen)
                seen[name] = group

    def test_gui_shim_reexports_same_objects(self):
        from core.catalogue_groups_data import GROUP_MEMBERS
        try:
            import gui.skills_catalog_data as shim
        except ImportError:
            # System python has no PySide6: the Qt shim cannot import
            # here — verify the re-export statically instead; the Qt
            # run proves the identity dynamically.
            src = (TREE / "gui" / "skills_catalog_data.py").read_text(
                encoding="utf-8")
            self.assertIn(
                "from core.gui_facade import catalogue_groups_data", src)
            self.assertIn(
                "GROUP_MEMBERS = catalogue_groups_data.GROUP_MEMBERS",
                src)
            return
        self.assertIs(shim.GROUP_MEMBERS, GROUP_MEMBERS)

    def test_routers_import_no_ast_reparse(self):
        src = (TREE / "core" / "brain" / "domain_routers.py").read_text(
            encoding="utf-8")
        self.assertNotIn("ast.parse", src)
        self.assertIn("catalogue_groups_data", src)
        self.assertNotIn("catalogue_path", src)

    def test_snapshot_fallback_on_missing_source(self):
        from core.brain import domain_routers as routers_mod
        live = routers_mod.catalogue_groups()
        saved_cache = routers_mod._GROUPS_CACHE
        saved_source = routers_mod._catalogue_source
        routers_mod._GROUPS_CACHE = None
        routers_mod._catalogue_source = lambda: (_ for _ in ()).throw(
            ImportError("catalogue_groups_data gone"))
        try:
            fallback = routers_mod.catalogue_groups()
            self.assertEqual(fallback, live)
            self.assertIs(routers_mod.catalogue_groups(), fallback)
        finally:
            routers_mod._catalogue_source = saved_source
            routers_mod._GROUPS_CACHE = saved_cache

    def test_agreement_check_fires_both_ways(self):
        from core.funnel import tools_registry as reg_mod
        from core import catalogue_groups_data as cat_mod
        # Ungrouped + unexcused tool must raise.
        reg_mod.tools._tools["fake_tool_gaps23"] = {
            "description": "x", "parameters": {}}
        try:
            with self.assertRaises(ValueError) as ctx:
                reg_mod._check_catalogue_agreement()
            self.assertIn("fake_tool_gaps23", str(ctx.exception))
        finally:
            del reg_mod.tools._tools["fake_tool_gaps23"]
        # Grouped but unregistered must raise (patch the table, not
        # the registry, so the registry's insertion order is untouched).
        saved_groups = cat_mod.GROUP_MEMBERS
        patched = {g: tuple(n for n in names
                            if n != "get_current_time_date")
                   for g, names in saved_groups.items()}
        cat_mod.GROUP_MEMBERS = patched
        try:
            with self.assertRaises(ValueError) as ctx:
                reg_mod._check_catalogue_agreement()
            self.assertIn("get_current_time_date", str(ctx.exception))
        finally:
            cat_mod.GROUP_MEMBERS = saved_groups
        reg_mod._check_catalogue_agreement()


class Fv8DeclarativeTests(unittest.TestCase):
    def test_packs_table_exact_order(self):
        self.assertEqual(tuple(n for n, _ in PACKS), PACKS_EXPECTED)
        self.assertEqual(len(PACKS), 92)
        for _, registrar in PACKS:
            self.assertTrue(hasattr(tools, registrar), registrar)

    def test_no_import_time_register_calls(self):
        src = (TREE / "core" / "funnel" / "tools_registry.py").read_text(
            encoding="utf-8")
        self.assertEqual(
            len(re.findall(r"tools\._register_\w+_tools\(\)", src)), 0)

    def test_toolspec_frozen_and_register_spec(self):
        def _probe_handler(args):
            return {"ok": True}

        spec = ToolSpec(
            name="gaps23_probe", description="F-V8 probe tool",
            parameters={"type": "object",
                        "properties": {"x": {"type": "integer"}}},
            handler=_probe_handler)
        self.assertTrue(spec.__dataclass_params__.frozen)
        fresh = ToolRegistry()
        before = len(fresh._tools)
        fresh.register_spec(spec)
        self.assertEqual(len(fresh._tools), before + 1)
        self.assertIn("gaps23_probe", fresh._tools)
        # The stored handler inherits the funnel boundary.
        with self.assertRaises(RuntimeError):
            fresh._tools["gaps23_probe"]["handler"]({"x": 1})
        # REG-01: re-registering the identical spec no-ops.
        fresh.register_spec(spec)
        self.assertEqual(len(fresh._tools), before + 1)
        with self.assertRaises(Exception):
            spec.name = "other"


class CensusTests(unittest.TestCase):
    def test_census_unchanged(self):
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 764)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(len({d["name"] for d in decls}), 764)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        params = sum(len(d["parameters"].get("properties", {}))
                   for d in decls)
        self.assertEqual(params, 2102)  #Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        closed = sum(1 for d in decls
                     if d["parameters"].get("additionalProperties")
                     is False)
        self.assertEqual(closed, 763)  # INTENTIONAL — field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        from core.catalogue_groups_data import GROUP_MEMBERS
        self.assertEqual(
            sum(len(v) for v in GROUP_MEMBERS.values()), 761)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        from core.funnel.tools_gate_confirm import _CONFIRM_GATE_TOOLS
        self.assertEqual(len(_CONFIRM_GATE_TOOLS), 169)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169
        snap = json.loads((TREE / "tests"
                           / "_classification_snapshot.json").read_text(
                               encoding="utf-8"))
        self.assertEqual(len(snap), 764)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169


if __name__ == "__main__":
    unittest.main()
