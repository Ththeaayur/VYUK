"""Audit fix Batch 2 — SCHEMAS / ROUTING / DECLARATIONS
(TOOL-01, TOOL-02, TOOL-03, ROUTE-01, ROUTE-02, DECL-02).

Owner-approved programme, working tree only, unshipped. Every test
drives the real registry / ranker / rig code; no audit repro script
is imported and no audit artefact enters the tree. Fakes appear
only at the embedder seam (toolbox.set_embedder, the documented
injection seam) and as temporary registry entries that are removed
in a finally block. DECL-01 is NEEDS-RULING and is NOT changed:
the legacy all-tools-direct builder is pinned to keep raising
over budget, and the per-profile budget check rides along as a
pure addition.

Data note: the few execute() calls here write the usual funnel
artefacts (data/activity.log, data/audit.jsonl); the batch
discipline restores data/ delete-first after runs.
"""

import unittest

# Phase 4 D1 INTENTIONAL: staged eval-rig import removed with its deleted module (plan §4.1 D-2).
from core.funnel import toolbox as tb  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)
from core.funnel.tools_registry import OPEN_SCHEMA_TOOLS  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _decl(name):
    return next(d for d in tools.get_declarations() if d["name"] == name)


class Tool01Tests(unittest.TestCase):
    def test_omitted_field_behaves_as_missing(self):
        self.assertIsNone(tools.validate_args("iot_network_check", {}))
        omitted = tools.execute("iot_network_check", {})
        self.assertEqual(omitted["status"], "success")
        self.assertIn('"status": "missing"', omitted["result"])
        self.assertIn("network_label", omitted["result"])

    def test_empty_field_matches_omitted(self):
        empty = tools.execute("iot_network_check", {"network_label": ""})
        self.assertEqual(empty["status"], "success")
        self.assertIn('"status": "missing"', empty["result"])

    def test_supplied_label_returns_local_administered(self):
        out = tools.execute("iot_network_check",
                            {"network_label": "home"})
        self.assertEqual(out["status"], "success")
        self.assertIn("local-administered as supplied", out["result"])
        self.assertNotIn('"status": "missing"', out["result"])

    def test_registry_consistency_guard_at_zero(self):
        """Audit's guard: every schema-required field is a handler
        parameter, and every handler parameter without a default
        is schema-required. (Defaulted parameters absent from a
        schema are pre-existing and out of this guard's scope;
        they run on their defaults, so no dispatch drop bites.)"""
        import inspect
        violations = []
        for name, entry in tools._tools.items():
            params = inspect.signature(entry["handler"]).parameters
            fixed = {k for k, p in params.items()
                     if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                   inspect.Parameter.KEYWORD_ONLY)}
            defaults = {k for k, p in params.items()
                        if p.default is not inspect.Parameter.empty}
            schema = entry.get("parameters") or {}
            required = set(schema.get("required") or [])
            if not required <= fixed:
                violations.append((name, "required-not-param",
                                   sorted(required - fixed)))
            if not (fixed - defaults) <= required:
                violations.append((name, "nodefault-not-required",
                                   sorted((fixed - defaults) - required)))
        self.assertEqual(violations, [])


class Tool02Tests(unittest.TestCase):
    def test_full_registry_closed_except_use_toolbox(self):
        decls = tools.get_declarations()
        # Library sweep INTENTIONAL (641 -> 645): four funnel tools; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (closed tool; closed count 652 -> 653 with wallpaper_apply, 653 -> 654 with wallpaper_revert); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        # added (teach_record_sequence, teach_list_sequences,
        # teach_replay_sequence, wallpaper_state); closed/open
        # shape unchanged (use_toolbox stays the only open tool).
        self.assertEqual(len(decls), 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        closed = [d for d in decls
                  if (d.get("parameters") or {}).get("additionalProperties")
                  is False]
        open_names = [d["name"] for d in decls
                      if (d.get("parameters") or {}).get("additionalProperties")
                      is not False]
        self.assertEqual(len(closed), 755)  # INTENTIONAL: 648 -> 651 (§7 consumers batch 2: +3 closed tools), see above; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; stale-pin sweep: INTENTIONAL — closed count 652 -> 653: wallpaper_apply added (additionalProperties False, so the new tool is closed); wallpaper-revert batch (2026-11-05): INTENTIONAL — closed count 653 -> 654: wallpaper_revert added (no parameters, so the new tool is closed); B-D1 (2026-11-09): INTENTIONAL — closed count 654 -> 655: window_place added (additionalProperties False, so the new tool is closed); B-R1-8 (2026-11-09): INTENTIONAL — closed count 655 -> 656: watch_gestures added (additionalProperties False, so the new tool is closed); R7-4 meetily (2026-11-10): INTENTIONAL — closed count 656 -> 660: start_meeting, stop_meeting, meeting_status, transcribe_audio added (additionalProperties False by the registry default, so the new tools are closed); R7-5 kovrel (2026-11-10): INTENTIONAL — closed count 660 -> 664: start_companion, stop_companion, companion_status, companion_pairing added (additionalProperties False by the registry default, so the new tools are closed); R7-6 micro-wake-word (2026-11-10): INTENTIONAL — closed count 664 -> 668: train_custom_wake_word, enable_custom_wake_model, list_custom_wake_models, custom_wake_model_status added (additionalProperties False by the registry default, so the new tools are closed); R7-7 taskflow (2026-11-10): INTENTIONAL — closed count 668 -> 672: define_workflow, run_workflow, list_workflows, workflow_runs added (additionalProperties False by the registry default, so the new tools are closed); R7-8 proactive-agent (2026-11-10): INTENTIONAL — closed count 672 -> 676: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results added (additionalProperties False by the registry default, so the new tools are closed); R8-1 fastembed port (2026-11-10): INTENTIONAL — closed count 676 -> 679: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10) added (additionalProperties False by the registry default, so the new tools are closed); R8-3 sumy port (2026-11-10): INTENTIONAL — closed count 679 -> 680: summarise_text added (additionalProperties False by the registry default, so the new tool is closed); R8-4 screen-break port (2026-11-10): INTENTIONAL — closed count 680 -> 683: eye_rest_timer, breathing_guide, log_glass added (additionalProperties False by the registry default, so the new tools are closed); R8-5 magic-wormhole port (2026-11-10): INTENTIONAL — closed count 683 -> 686: wormhole_offer, wormhole_receive, wormhole_status added (additionalProperties False by the registry default, so the new tools are closed); R8-6 yasb port (2026-11-10): INTENTIONAL — closed count 686 -> 687: status_strip added (additionalProperties False by the registry default, so the new tool is closed); R8-7 basic-pitch port (2026-11-10): INTENTIONAL — closed count 687 -> 689: audio_to_midi + audio_to_midi_status added (additionalProperties False by the registry default, so the new tools are closed); R8-8 mftool port (2026-11-10): INTENTIONAL — closed count 689 -> 691: fund_returns, mutual_fund_nav added (additionalProperties False by the registry default, so the new tools are closed); R8-9 pypresence adoption (2026-11-10): INTENTIONAL — closed count 691 -> 693: discord_presence + set_discord_client_id added (additionalProperties False by the registry default, so the new tools are closed); R9-2 rustic adoption (2026-11-10): INTENTIONAL — closed count 693 -> 698: init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status added (additionalProperties False by the registry default, so the new tools are closed); R9-3 whoosh port (2026-11-10): INTENTIONAL — closed count 698 -> 701: index_files, search_indexed_files, disk_index_status added (additionalProperties False by the registry default, so the new tools are closed); R9-4 mutagen port (2026-11-10): INTENTIONAL — closed count 701 -> 703: sync_folders_two_way, two_way_sync_status added (additionalProperties False by the registry default, so the new tools are closed); R9-8 elodie port (2026-11-10): 703 -> 705 — organise_photos, preview_photo_organisation added (additionalProperties False by the registry default, so the new tools are closed); R10-1 chrome-devtools-mcp port (2026-11-10): INTENTIONAL — closed count 705 -> 708: browser_console, browser_network, browser_performance added (additionalProperties False by the registry default, so the new tools are closed); R10-3 browser-history port (2026-11-10): INTENTIONAL — closed count 708 -> 710: browser_history, browser_bookmarks added (additionalProperties False by the registry default, so the new tools are closed); R10-4 python-adblock port (2026-11-10): INTENTIONAL — closed count 710 -> 712: check_url_blocked, filter_list_status added (additionalProperties False by the registry default, so the new tools are closed); R10-5 python-readability port (2026-11-10): INTENTIONAL — closed count 712 -> 713: extract_article added (additionalProperties False by the registry default, so the new tool is closed); R11-1 ollama port (2026-11-10): INTENTIONAL — closed count 713 -> 716: ollama_status (no parameters), ollama_pull + ollama_delete added (additionalProperties False by the registry default, so the new tools are closed); R16-8 event-planning pack (2026-11-11): INTENTIONAL — closed count 716 -> 722: six tools added (additionalProperties False by the registry default, so the new tools are closed); R16-9 attic pack (2026-11-11): INTENTIONAL — closed count 722 -> 723: home_inventory added (additionalProperties False by the registry default, so the new tool is closed); R16-10 GreaterWMS pack (2026-11-11): INTENTIONAL — closed count 723 -> 724: warehouse_stock added (additionalProperties False by the registry default, so the new tool is closed); R17-1 bookrail pack (2026-11-11): 724 -> 725 — bookings added (additionalProperties False by the registry default, so the new tool is closed); R17-2 trivy pack (2026-11-11): 725 -> 726 — security_scan added (additionalProperties False by the registry default, so the new tool is closed); R17-3 go2rtc pack (2026-11-11): 726 -> 727 — ip_camera added (additionalProperties False by the registry default, so the new tool is closed); R17-4 MCSManager pack (2026-11-11): 727 -> 728 — game_server added (additionalProperties False by the registry default, so the new tool is closed); R17-5 komga pack (2026-11-11): 728 -> 729 — ebook_library added (additionalProperties False by the registry default, so the new tool is closed); R17-6 openstays pack (2026-11-11): 729 -> 730 — lodging added (additionalProperties False by the registry default, so the new tool is closed); R17-7 qlcplus pack (2026-11-12): 730 -> 731 — stage_lighting added (additionalProperties False by the registry default, so the new tool is closed); R17-8 pvlib pack (2026-11-12): 731 -> 732 — solar_yield added (additionalProperties False by the registry default, so the new tool is closed); R17-9 wardrobe pack (2026-11-12): 732 -> 733 — wardrobe added (additionalProperties False by the registry default, so the new tool is closed); R18-1 stripe pack (2026-11-12): 733 -> 734 — billing added (additionalProperties False by the registry default, so the new tool is closed); R18-3 mcp-context-forge pack (2026-11-12): 734 -> 735 — mcp_registry added (additionalProperties False by the registry default, so the new tool is closed); R18-4 a2a pack (2026-11-12): 735 -> 736 — agent_call added (additionalProperties False by the registry default, so the new tool is closed); R18-5 dspy pack (2026-11-12): 736 -> 737 — prompt_tune added (additionalProperties False by the registry default, so the new tool is closed); R18-6 fonoster pack (2026-11-12): 737 -> 738 — voice_agent added (additionalProperties False by the registry default, so the new tool is closed); R18-7 attendize pack (2026-11-12): 738 -> 739 — event_tickets added (additionalProperties False by the registry default, so the new tool is closed); R18-8 ta pack (2026-11-12): 739 -> 740 — technical_analysis added (additionalProperties False by the registry default, so the new tool is closed); R18-10 timetrace pack (2026-11-12): 740 -> 741 — time_tracker added (additionalProperties False by the registry default, so the new tool is closed); R19-1 videolingo pack (2026-11-12): 741 -> 742 — audio_localise added (additionalProperties False by the registry default, so the new tool is closed); R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine
        self.assertEqual(open_names, ["use_toolbox"])
        self.assertEqual(OPEN_SCHEMA_TOOLS, frozenset({"use_toolbox"}))

    def test_undeclared_key_refuses_on_effectful_tool(self):
        problem = tools.validate_args(
            "write_file",
            {"filepath": "/tmp/x.txt", "content": "hi", "bogus": 1})
        self.assertIsNotNone(problem)
        self.assertIn("bogus", problem)
        self.assertIn("not a declared argument", problem)

    def test_undeclared_key_refuses_on_read_tool(self):
        problem = tools.validate_args("get_current_time_date",
                                      {"bogus": 1})
        self.assertIsNotNone(problem)
        self.assertIn("bogus", problem)

    def test_use_toolbox_stays_open_at_validation(self):
        self.assertIsNone(tools.validate_args(
            "use_toolbox",
            {"action": "find", "query": "time", "bogus": 1}))

    def test_dispatch_reports_dropped_keys(self):
        def handler(path, content):
            return "ran"
        args, dropped = tools._dispatch_args(
            "fake_tool", handler, {"path": "p", "content": "c",
                                   "bogus": 1})
        self.assertEqual(args, {"path": "p", "content": "c"})
        self.assertEqual(dropped, ["bogus"])

    def test_dispatch_refuses_effectful_open_tool(self):
        """Belt-and-braces behind validation: an open-schema
        effectful tool that reaches dispatch with an undeclared
        key must refuse, never run stripped."""
        def fake_effectful(path, content):
            return "ran"
        entry = {"name": "auditfix_b2_fake_effectful",
                 "description": "test-only",
                 "parameters": {"type": "object", "properties": {
                     "path": {"type": "string"},
                     "content": {"type": "string"}},
                     "required": ["path", "content"]},
                 "handler": fake_effectful}
        tools._tools[entry["name"]] = entry
        try:
            out = tools.execute(entry["name"],
                                {"path": "p", "content": "c",
                                 "window_titel": "x"})
            self.assertEqual(out["status"], "refused")
            self.assertIn("window_titel", out["message"])
            self.assertIn("NOT run", out["message"])
        finally:
            tools._tools.pop(entry["name"], None)

    def test_dispatch_surfaces_drop_on_read_tool(self):
        entry = tools._tools["get_current_time_date"]
        saved = entry["parameters"]
        open_params = dict(saved)
        open_params.pop("additionalProperties", None)
        entry["parameters"] = open_params
        try:
            out = tools.execute("get_current_time_date", {"bogus": 1})
            self.assertEqual(out["status"], "success")
            self.assertIn("bogus", out["result"])
            self.assertIn("does not accept", out["result"])
        finally:
            entry["parameters"] = saved

    def test_gemini_copy_strips_additional_properties(self):
        decls = tb.gemini_decl_for_names(
            tools, ["base_convert", "get_current_time_date"])
        for decl in decls:
            self.assertNotIn("additionalProperties",
                             decl.get("parameters") or {})


class Tool03Tests(unittest.TestCase):
    def test_four_typeless_props_now_typed_anyof(self):
        for name, prop in (("base_convert", "from_base"),
                           ("base_convert", "to_base"),
                           ("epoch_convert", "value"),
                           ("roman_convert", "value")):
            spec = _decl(name)["parameters"]["properties"][prop]
            self.assertEqual(spec.get("anyOf"),
                             [{"type": "integer"}, {"type": "string"}],
                             f"{name}.{prop}")

    def test_anyof_accepts_int_and_string_rejects_rest(self):
        cases = [
            ("base_convert", {"value": "10", "from_base": 2,
                              "to_base": 16}),
            ("base_convert", {"value": "10", "from_base": "binary",
                              "to_base": 16}),
            ("epoch_convert", {"value": 1700000000}),
            ("epoch_convert", {"value": "2026-10-22T08:00:00"}),
            ("roman_convert", {"value": 42}),
            ("roman_convert", {"value": "XLII"}),
        ]
        for name, args in cases:
            self.assertIsNone(tools.validate_args(name, args),
                              f"{name} {args}")
        bad = [
            ("base_convert", {"value": "10", "from_base": True,
                              "to_base": 16}),
            ("base_convert", {"value": "10", "from_base": None,
                              "to_base": 16}),
            ("base_convert", {"value": "10", "from_base": {},
                              "to_base": 16}),
            ("base_convert", {"value": "10", "from_base": [2],
                              "to_base": 16}),
            ("epoch_convert", {"value": None}),
            ("epoch_convert", {"value": 1.5}),
            ("roman_convert", {"value": True}),
            ("roman_convert", {"value": {"n": 1}}),
        ]
        for name, args in bad:
            self.assertIsNotNone(tools.validate_args(name, args),
                                 f"{name} {args} should fail")

    def test_schema_lint_zero_violations(self):
        """The lint itself: every root property carries a
        description and a type/anyOf/enum/const. Re-derived
        live: 1401 properties, 0 missing (was 75 missing).
        Audit fix Batch 4 (DESK/DISPLAY) — INTENTIONAL: +3
        properties (take_screenshot.monitor, screen_diff.monitor,
        focus_window_by_text.title_hint); the lint's real checks
        (0 missing descriptions, 0 typeless) are unchanged.

        Library sweep INTENTIONAL (1401 -> 1409): +8 properties
        from the four new funnel tools; the real checks
        (0 missing descriptions, 0 typeless) are unchanged.
        §7 consumers batch 1 INTENTIONAL (1409 -> 1440): +31
        properties from the four §7 consumer tools (menu 6,
        dialog 7, surfaces 13, adapter 5); the real checks
        (0 missing descriptions, 0 typeless) are unchanged.
        §7 consumers batch 2 INTENTIONAL (1440 -> 1469): +29
        §7 consumers batch 8 INTENTIONAL (1469 -> 1477): +8 properties from the one §7 consumer tool (context 8: action, target, target_handle_id, window_handle_id, snapshot, path, confirmed, effectful); the real checks (0 missing descriptions, 0 typeless) are unchanged.
        §7 consumers batch 11 INTENTIONAL (1477 -> 1478): +1 property from the new optional monitor argument on cursor_control (no new tool); the real checks (0 missing descriptions, 0 typeless) are unchanged.
        Schema-lint bump INTENTIONAL (1478 -> 1481): +3 properties from wallpaper_apply (hour, entries, art_dir); the real checks (0 missing descriptions, 0 typeless) are unchanged.
        Wallpaper-apply gates batch INTENTIONAL (1481 -> 1482): +1 property from the new opt_in argument on wallpaper_apply; the real checks (0 missing descriptions, 0 typeless) are unchanged.
        Wallpaper multi-monitor batch (R4) INTENTIONAL (1482 -> 1486): +4 properties from the new optional monitor argument on wallpaper_apply, wallpaper_revert, set_wallpaper and display_control (no new tool); the real checks (0 missing descriptions, 0 typeless) are unchanged.
        Skill contained-runtime pilot (B8) INTENTIONAL (1486 -> 1487): +1 property from the new optional inputs argument on personal_skills (no new tool); the real checks (0 missing descriptions, 0 typeless) are unchanged.
        Meeting capture (R7-4 meetily) INTENTIONAL (1498 -> 1506): +2 properties — start_meeting adds title, transcribe_audio adds filename; the real checks (0 missing descriptions, 0 typeless) are unchanged.
        properties from the three §7 consumer tools (drag 11,
        monitor 10, journal 8); the real checks
        (0 missing descriptions, 0 typeless) are unchanged."""
        total, missing_desc, typeless = 0, [], []
        for decl in tools.get_declarations():
            props = (decl.get("parameters") or {}).get("properties") or {}
            for prop, spec in props.items():
                total += 1
                if isinstance(spec, dict):
                    if "description" not in spec:
                        missing_desc.append((decl["name"], prop))
                    if not any(k in spec for k in
                               ("type", "anyOf", "enum", "const")):
                        typeless.append((decl["name"], prop))
        self.assertEqual(total, 2078)  # INTENTIONAL: 1513 -> 1530 — score_intervention adds 11 properties (candidate_text, kind, confidence, evidence_samples, explicit_request, routine_due, dismissed_recently, user_mid_task, watcher_kind, prediction_available, threshold), run_proactivity_bench adds 1 property (threshold), list_proactivity_scores adds 1 property (limit), proactivity_bench_results adds 1 property (limit), R7-8 proactive-agent 2026-11-10; R8-1 fastembed port (2026-11-10): 1527 -> 1530 — semantic_search_knowledge adds 2 properties (query, limit), embed_knowledge adds 1 property (limit), knowledge_embedding_status adds 0; R8-3 sumy port (2026-11-10): 1530 -> 1532 — summarise_text adds 2 properties (text, max_sentences); R8-4 screen-break port (2026-11-10): 1532 -> 1538 — eye_rest_timer adds 3 properties (action, interval_minutes, snooze_minutes), breathing_guide adds 2 properties (pattern, cycles), log_glass adds 1 property (action); R8-5 magic-wormhole port (2026-11-10): 1538 -> 1544 — wormhole_offer adds 3 properties (action, file, expiry_minutes), wormhole_receive adds 3 properties (phrase, sender, destination), wormhole_status adds 0 properties; R8-6 yasb port (2026-11-10): 1544 -> 1545 — status_strip adds 1 property (action); R8-7 basic-pitch port (2026-11-10): 1545 -> 1551 — audio_to_midi adds 6 properties (audio_path, output, onset_threshold, frame_threshold, min_note_length_ms, tempo_bpm); audio_to_midi_status adds 0; R8-8 mftool port (2026-11-10): 1551 -> 1557 — fund_returns adds 5 properties (start_value, end_value, years, cashflows, currency), mutual_fund_nav adds 1 property (scheme); R8-9 pypresence adoption (2026-11-10): 1557 -> 1563 — discord_presence adds 5 properties (action, details, state, name, client_id); set_discord_client_id adds 1 property (client_id); R9-2 rustic adoption (2026-11-10): 1563 -> 1572 — init_backup_repo adds 1 property (repo_path); backup_files adds 3 (sources, repo_path, label); list_backup_snapshots adds 1 (repo_path); restore_backup adds 3 (snapshot, destination, repo_path); backup_status adds 1 (repo_path); R9-3 whoosh port (2026-11-10): 1572 -> 1576 — index_files adds 2 properties (folders, rebuild), search_indexed_files adds 2 properties (query, limit), disk_index_status adds 0; R9-4 mutagen port (2026-11-10): 1576 -> 1580 — sync_folders_two_way adds 4 properties (folder_a, folder_b, mirror_delete, dry_run); two_way_sync_status adds none; R9-8 elodie port (2026-11-10): 1580 -> 1585 — organise_photos adds 3 properties (source, destination, dry_run); preview_photo_organisation adds 2 properties (source, destination); R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console adds 2 properties (seconds, target), browser_network adds 3 (seconds, target, max_entries), browser_performance adds 1 (target); R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine
        self.assertEqual(missing_desc, [])
        self.assertEqual(typeless, [])


class Route01Tests(unittest.TestCase):
    AUDIT_FALSE = [
        ("convert a PDF to Word", "unit_convert"),
        ("read the content of this webpage", "open_path"),
        ("translate this text", "type_text"),
        ("record my screen", "take_screenshot"),
        ("take a screenshot of the second monitor", "take_screenshot"),
    ]
    TRUE_POSITIVES = [
        ("open the browser and search for a paper", "browser_control"),
        ("take a screenshot and save it to the desktop",
         "take_screenshot"),
        ("convert 5 miles to kilometres", "unit_convert"),
        ("type this text into the active window", "type_text"),
        ("check my battery level", "battery_info"),
    ]

    def test_false_confidence_probes_no_longer_inline(self):
        for query, wrong in self.AUDIT_FALSE:
            ranked = tb.rank_tools_keyword(tools, query, limit=8)
            self.assertNotIn(wrong, ranked,
                             f"{query!r} still ranks {wrong}")
            self.assertNotEqual(ranked[0] if ranked else None, wrong)
            scores = tb.ranking_scores(tools, query)
            self.assertFalse(tb.find_may_inline(query, scores, ranked[0]))

    def test_false_confidence_probes_abstain_or_list_not_inline(self):
        for query, wrong in self.AUDIT_FALSE:
            text = tb.use_toolbox(action="find", query=query)
            self.assertNotIn("confident match", text,
                             f"{query!r} still inlines")
            self.assertNotIn("its full declaration follows", text)

    def test_unsupported_tasks_get_plain_abstention(self):
        for query in ("translate this text",
                      "convert a PDF to Word", "record my screen"):
            text = tb.use_toolbox(action="find", query=query)
            self.assertTrue(text.startswith("No suitable tool"),
                            f"{query!r}: {text[:120]}")

    def test_true_positives_still_inline(self):
        for query, expected in self.TRUE_POSITIVES:
            ranked = tb.rank_tools_keyword(tools, query, limit=8)
            self.assertEqual(ranked[0], expected, query)
            scores = tb.ranking_scores(tools, query)
            self.assertTrue(tb.find_may_inline(query, scores, ranked[0]),
                            query)
            text = tb.use_toolbox(action="find", query=query)
            self.assertIn("confident match", text, query)

    def test_exact_name_query_exempt_from_negative_penalty(self):
        ranked = tb.rank_tools_keyword(tools, "unit_convert", limit=8)
        self.assertEqual(ranked[0], "unit_convert")

    def test_find_states_keyword_only_mode_honestly(self):
        text = tb.use_toolbox(action="find",
                              query="open the browser and search")
        self.assertIn("keyword-only (no embedding model loaded)", text)


# Phase 4 D1 INTENTIONAL: staged eval-rig test class removed with its deleted module (plan §4.1 D-2); no other test touched.
class DeclTests(unittest.TestCase):
    def test_doctor_label_counts_honestly(self):
        import doctor as doctor_mod
        seen = []
        saved = doctor_mod.report
        doctor_mod.report = lambda s, l, d="", f="": seen.append((s, l, d))
        try:
            doctor_mod.check_tool_router()
        finally:
            doctor_mod.report = saved
        router = [d for s, l, d in seen if l == "Tool router"]
        self.assertEqual(len(router), 1)
        detail = router[0]
        self.assertIn("58 offered declarations", detail)
        self.assertIn("(57 resident tools + use_toolbox)", detail)
        self.assertNotIn("58 resident tools", detail)

    def test_legacy_builder_retired(self):
        """DECL-01 RETIRED (owner ruling 2026-11-01,
        overruling the reviewer's label-diagnostic-only
        recommendation): the legacy all-tools-direct builder
        no longer exists; offered sets are built by the
        profile builders."""
        self.assertFalse(hasattr(tools, "get_gemini_declarations"))

    def test_offered_profiles_under_budget(self):
        """Ruling-free DECL-01 companion: the profiles sessions
        actually offer stay under the 240,000-byte budget."""
        import json
        from core.funnel import tool_profiles  # Phase 3 F2a INTENTIONAL: tool_profiles moved to core/funnel/
        for profile in ("assistant", "desktop", "full"):
            decls = tool_profiles.profile_declarations(tools, profile)
            self.assertLess(len(json.dumps(decls)), 240_000, profile)


if __name__ == "__main__":
    unittest.main()
