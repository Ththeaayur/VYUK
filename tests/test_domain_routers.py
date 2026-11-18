"""Tests for domain routers (owner-approved design, 2026-10-27).

What is proven here:
* routers partition EXACTLY the non-core set minus the
  use_toolbox meta-tool: every tool NOT
  in toolbox.CORE_TOOL_PRIORITY, except use_toolbox
  (offered directly in routed sessions, absent in
  router-off), in exactly one router, no
  core tool in any router, nothing else dropped, no duplicates —
  checked against GROUP_MEMBERS read independently from
  gui/skills_catalog_data.py source (AST, no Qt import);
* every offered set (assistant, desktop, full, router-off)
  measures under the 240,000-byte hard cap, and every one
  still builds with the legacy all-direct builder retired
  (DECL-01, owner ruling 2026-11-01) — after this build NO
  offered-set builder builds a fitted full list;
* dispatch through the funnel: a read-only tool executes
  via its router; a confirm-gated tool fails closed with
  no handler registered (the funnel's own not_confirmed
  marker, nothing run); unknown tools and wrong-router
  calls refuse, the wrong-router refusal naming the
  correct router; a validation failure carries the tool's
  schema, so the model repairs in one round (the v5.50
  funnel pattern, verified present — not re-implemented
  in the router);
* router names are valid, unique function names; each
  router's tool enum exactly equals its membership; each
  purpose line is capped at 80 chars and is a purpose,
  never the tool's full description;
* building routers never touches the registry (641 tools after research wiring
  before and after) and no registered tool is named
  route_* (so the Live dispatch branch can never shadow
  a real tool).

No tool is renamed, no resident set is changed, no
description is trimmed by this build, and no test here
weakens a fitted-cap assertion: the fitted list is retired
by owner ruling (DECL-01, 2026-11-01), and the offered
sets are measured directly against the cap.
"""

import json
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from core import confirm_gate
from core.brain import domain_routers as routers_mod  # Phase 3 B1b INTENTIONAL: domain_routers moved to core/brain/
from core.voice import live_client  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.funnel import tool_profiles as profiles  # Phase 3 F2a INTENTIONAL: tool_profiles moved to core/funnel/
from core.funnel.toolbox import CORE_TOOL_PRIORITY  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

BUDGET = 240_000


def _catalogue_groups():
    """GROUP_MEMBERS from the single catalogue source
    (core/catalogue_groups_data.py — pure data, no Qt)."""
    # GAPS-23 (F-V7): the AST re-parse is gone with the Qt
    # module that forced it — the tables live in core now.
    from core.catalogue_groups_data import GROUP_MEMBERS
    return dict(GROUP_MEMBERS)

class TestDomainRouters(unittest.TestCase):
    def test_registry_count_unchanged_by_building_routers(self):
        before = len(tools.get_declarations())
        routers_mod.build_domain_routers(tools)
        routers_mod.build_domain_routers(tools)
        after = len(tools.get_declarations())
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # INTENTIONAL UPDATE (Phase 3 B1b, 2026-11-02): 653, not 641. The registry grew by 12 since the pin was set (library wiring +4, §7 consumers batch 1 +4, batch 2 +3, batch 8 +1); building routers still changes nothing — before == after == 653. Proven: identical 653 != 641 failure on the pristine B1b backup tree.
        # INTENTIONAL UPDATE (B-D1, 2026-11-09): 656, not 655 — window_place added; building routers still changes nothing. (Wallpaper-revert batch, 2026-11-05: 655, not 654.)
        # INTENTIONAL UPDATE (B-R1-8, 2026-11-09): 657, not 656 — watch_gestures added; building routers still changes nothing.
        # INTENTIONAL UPDATE (R7-4 meetily, 2026-11-10): 661, not 657 — start_meeting, stop_meeting, meeting_status, transcribe_audio added; building routers still changes nothing.
        # INTENTIONAL UPDATE (R7-5 kovrel, 2026-11-10): 665, not 661 — start_companion, stop_companion, companion_status, companion_pairing added; building routers still changes nothing.
        self.assertEqual(before, 756, before)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(after, 756, after)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_coverage_partition_matches_catalogue(self):
        groups = _catalogue_groups()
        registered = {d["name"] for d in tools.get_declarations()}
        core = set(CORE_TOOL_PRIORITY)
        expected: dict[str, set[str]] = {}
        grouped: set[str] = set()
        for group, members in groups.items():
            grouped.update(m for m in members if m in registered)
            keep = {m for m in members
                    if m in registered and m not in core
                    and m not in routers_mod.META_TOOL_NAMES}
            if keep:
                expected[routers_mod.router_name_for_group(group)] = keep
        ungrouped = {n for n in registered - grouped
                     if n not in core
                     and n not in routers_mod.META_TOOL_NAMES}
        if ungrouped:
            fallback = routers_mod.router_name_for_group(
                routers_mod.FALLBACK_GROUP)
            expected.setdefault(fallback, set()).update(ungrouped)
        membership = routers_mod.domain_router_membership(tools)
        self.assertEqual(set(membership), set(expected),
                         f"router names differ: {set(membership) ^ set(expected)}")
        for rname, members in expected.items():
            self.assertEqual(set(membership[rname]), members, rname)
        all_routed: list[str] = []
        for members in membership.values():
            all_routed.extend(members)
        self.assertEqual(len(all_routed), len(set(all_routed)),
                         "a tool appears in two routers")
        self.assertEqual(set(all_routed),
                         registered - core - routers_mod.META_TOOL_NAMES,
                         "routers must cover exactly the non-core set "
                         "minus the meta-tool")
        self.assertFalse(set(all_routed) & core,
                         "a core tool leaked into a router")
        self.assertNotIn("use_toolbox", set(all_routed),
                         "the meta-tool must live in no router: "
                         "router-off must mean no dispatcher")

    def test_router_names_valid_unique_and_enums_exact(self):
        membership = routers_mod.domain_router_membership(tools)
        routers = routers_mod.build_domain_routers(tools)
        self.assertEqual(len(routers), len(membership))
        seen: set[str] = set()
        by_name = {r["name"]: r for r in routers}
        for rname, members in membership.items():
            self.assertTrue(
                re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", rname),
                rname)
            self.assertNotIn(rname, seen, rname)
            seen.add(rname)
            decl = by_name[rname]
            enum = decl["parameters"]["properties"]["tool"]["enum"]
            self.assertEqual(enum, list(members),
                             f"{rname} enum != membership")
            self.assertEqual(
                set(decl["parameters"]["properties"]), {"tool", "args"},
                f"{rname} carries parameters beyond tool + args")

    def test_purposes_capped_and_not_full_descriptions(self):
        canonical = {d["name"]: d.get("description", "")
                     for d in tools.get_declarations()}
        membership = routers_mod.domain_router_membership(tools)
        for decl in routers_mod.build_domain_routers(tools):
            members = membership[decl["name"]]
            expected = "; ".join(
                f"{name}: {routers_mod._purpose(canonical[name])}"
                for name in members)
            self.assertTrue(
                decl["description"].endswith(expected),
                f"{decl['name']} description does not carry its "
                "members' purposes in order")
            for name in members:
                purpose = routers_mod._purpose(canonical[name])
                self.assertLessEqual(
                    len(purpose), routers_mod.PURPOSE_CAP, name)
                self.assertNotEqual(
                    purpose, canonical[name],
                    f"{name} router purpose is its full description")

    def test_purpose_derivation_on_synthetic_inputs(self):
        purpose = routers_mod._purpose
        self.assertEqual(purpose(""), "(no description)")
        self.assertEqual(
            purpose("Do a thing. Then do another thing."),
            "Do a thing.")
        self.assertEqual(
            purpose("Handle e.g. widgets and gadgets here. More text."),
            "Handle e.g. widgets and gadgets here.")
        long_text = ("This is a deliberately over-long purpose "
                     "sentence that must be cut short somehow.")
        cut = purpose(long_text)
        self.assertLessEqual(len(cut), routers_mod.PURPOSE_CAP, cut)
        self.assertTrue(cut.endswith("…"), cut)

    def test_offered_sets_bytes_and_shapes(self):
        assistant = profiles.profile_declarations(tools, "assistant")
        desktop = profiles.profile_declarations(tools, "desktop")
        full = profiles.profile_declarations(tools, "full")
        router_off = profiles.profile_declarations(
            tools, "desktop", include_toolbox=False)
        sizes = {
            "assistant": len(json.dumps(assistant)),
            "desktop": len(json.dumps(desktop)),
            "full": len(json.dumps(full)),
            "router-off": len(json.dumps(router_off)),
        }
        print(f"domain-router offered bytes: {sizes}")
        for label, size in sizes.items():
            self.assertLess(size, BUDGET, f"{label}: {size}")
        for decls, resident in (
                (assistant, CORE_TOOL_PRIORITY),
                (full, CORE_TOOL_PRIORITY)):
            names = [d["name"] for d in decls]
            self.assertEqual(names[:len(resident)], list(resident))
            self.assertEqual(names[-1], "use_toolbox")
            self.assertEqual(len(names), len(set(names)),
                             "duplicate declaration in offered set")
        desktop_names = [d["name"] for d in desktop]
        self.assertEqual(desktop_names[-1], "use_toolbox")
        router_off_names = [d["name"] for d in router_off]
        self.assertNotIn("use_toolbox", router_off_names,
                         "router-off must omit the use_toolbox declaration")
        self.assertTrue(any(n.startswith("route_") for n in router_off_names),
                        "router-off must still carry domain routers")
        for decls in (assistant, desktop, full):
            self.assertIn("use_toolbox",
                          [d["name"] for d in decls])

    def test_desktop_offers_named_time_tool(self):
        """Field-check fix #10 (V2): the desktop profile — the
        default Live profile — offers get_current_time_date
        directly, so a spoken named-tool demand ("what time is
        it") calls the tool instead of being answered in words.
        The assistant profile already offered it first; the
        desktop set did not, and the model does not route a
        by-name demand through use_toolbox. Pinned by count:
        58 offered (46 resident + 11 routers + use_toolbox),
        one more than the 56 the field check measured."""
        desktop = profiles.profile_declarations(tools, "desktop")
        names = [d["name"] for d in desktop]
        self.assertIn("get_current_time_date", names)
        self.assertEqual(len(names), 58)  # INTENTIONAL — field-check fix #32 (2026-11-18): 57 -> 58 — run_anticipation_routine joins the desktop profile
        # The Live-connect path itself, router on and off.
        for router_on in (True, False):
            decls, _routed, _label = live_client._offered_declarations(
                tools, SimpleNamespace(tool_router=router_on,
                                       tool_profile="desktop"))
            self.assertIn("get_current_time_date",
                          [d["name"] for d in decls],
                          f"router={router_on}")

    def test_offered_sets_build_with_legacy_builder_retired(self):
        # DECL-01 (owner ruling 2026-11-01): the legacy
        # all-direct builder is gone, so offered-set
        # independence from it is structural, not a
        # monkeypatched simulation — and every offered set
        # still builds under the budget.
        self.assertFalse(hasattr(tools, "get_gemini_declarations"))
        for profile in ("assistant", "desktop", "full"):
            decls = profiles.profile_declarations(tools, profile)
            self.assertLess(len(json.dumps(decls)), BUDGET, profile)
        off = profiles.profile_declarations(
            tools, "desktop", include_toolbox=False)
        self.assertLess(len(json.dumps(off)), BUDGET)
        for profile in ("assistant", "desktop", "full"):
            decls, _routed, _label = live_client._offered_declarations(
                tools, SimpleNamespace(tool_router=True,
                                       tool_profile=profile))
            self.assertLess(len(json.dumps(decls)), BUDGET, profile)
        decls, routed, label = live_client._offered_declarations(
            tools, SimpleNamespace(tool_router=False,
                                   tool_profile="desktop"))
        self.assertFalse(routed)
        self.assertEqual(label, "router-off")
        self.assertNotIn("use_toolbox",
                         [d["name"] for d in decls])
        self.assertLess(len(json.dumps(decls)), BUDGET)

    def test_dispatch_readonly_tool_via_router(self):
        out = routers_mod.dispatch_route(
            tools, "route_system_apps", "feel_orb_map", {"level": 0.5})
        self.assertEqual(out.get("status"), "success", out)
        result = out.get("result", "")
        if isinstance(result, str):
            result = json.loads(result)
        self.assertEqual(result.get("kind"), "ORB_MAPPING")

    def test_dispatch_meta_tool_hint_not_routed(self):
        out = routers_mod.dispatch_route(
            tools, "route_system_apps", "use_toolbox", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "failure" for unknown/unroutable calls (a failure, not a refusal); kind/message unchanged.
        self.assertEqual(out.get("status"), "failure", out)
        self.assertIn("directly", out.get("message", ""), out)

    def test_dispatch_string_args_coerced_like_use_toolbox(self):
        out = routers_mod.dispatch_route(
            tools, "route_system_apps", "feel_orb_map",
            '{"level": 0.5}')
        self.assertEqual(out.get("status"), "success", out)

    def test_dispatch_confirm_gated_fails_closed_without_handler(self):
        had_handler = confirm_gate.has_handler()
        saved = getattr(confirm_gate, "_handler", None)
        confirm_gate.clear_handler()
        try:
            self.assertFalse(confirm_gate.has_handler())
            out = routers_mod.dispatch_route(
                tools, "route_hands_eyes", "run_command",
                {"command": "echo hi"})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(out.get("status"), "refused", out)
            self.assertEqual(out.get("kind"), "not_confirmed", out)
            self.assertIn("needs the user's OK", out.get("message", ""))
            self.assertIn("no confirmation handler is active",
                          out.get("message", ""))
        finally:
            confirm_gate.register_handler(saved)
            self.assertEqual(confirm_gate.has_handler(), had_handler)

    def test_dispatch_unknown_and_wrong_router_refuse(self):
        unknown = routers_mod.dispatch_route(
            tools, "route_mind", "no_such_tool_xyz", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "failure" for unknown/unroutable calls (a failure, not a refusal); kind/message unchanged.
        self.assertEqual(unknown.get("status"), "failure")
        self.assertEqual(unknown.get("kind"), "unavailable")
        self.assertIn("Unknown tool", unknown.get("message", ""))
        wrong = routers_mod.dispatch_route(
            tools, "route_mind", "feel_orb_map", {"level": 0.5})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for a wrong-router refusal (it names the correct router); kind/message unchanged.
        self.assertEqual(wrong.get("status"), "refused")
        self.assertEqual(wrong.get("kind"), "refused")
        self.assertIn("route_system_apps", wrong.get("message", ""))
        no_router = routers_mod.dispatch_route(
            tools, "route_no_such_domain", "feel_orb_map", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "failure" for unknown/unroutable calls (a failure, not a refusal); kind/message unchanged.
        self.assertEqual(no_router.get("status"), "failure")
        self.assertIn("Unknown domain router", no_router.get("message", ""))

    def test_dispatch_validation_carries_schema(self):
        out = routers_mod.dispatch_route(
            tools, "route_system_apps", "feel_orb_map", {})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(out.get("status"), "refused", out)
        self.assertEqual(out.get("kind"), "validation", out)
        self.assertIsInstance(out.get("schema"), dict, out)
        self.assertIn("level",
                      out["schema"].get("parameters", {}).get("properties", {}))

    def test_no_registered_tool_shadows_router_dispatch(self):
        names = [d["name"] for d in tools.get_declarations()]
        self.assertFalse([n for n in names if n.startswith("route_")],
                         "a real tool named route_* would be "
                         "intercepted by the Live dispatch branch")


if __name__ == "__main__":
    unittest.main()
