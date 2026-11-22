"""Wave 8 Build 4 tests — feel, non-veto only, plus the Part 0
catalogue fix.

Proves: the orb mapping from measured levels (and refusal of
unmeasured/out-of-range levels); the HUD colour + honesty
rules (compliant descriptor passes, fake-data row, wrong
colour for a state, face, and vetoed token refuse whole);
procedural sfxr-style sounds render deterministically in
memory with no sample and no bundled file; a sound pack
without a primary licence read is refused, a CC-BY pack with
one is ledgered but never bundled; the OFL stack families
(Orbitron, Sora, Share Tech Mono) are LANDED and bundled
(B-R1-7) and no other family is cleared by analogy; the
particle budget tiers and degradation steps; tool
classifications and funnel confirm refusal without a handler;
and the catalogue fix — every registered tool except the 2
plugins (get_crypto_price, get_stock_quote, which the
catalogue never groups) appears in GROUP_MEMBERS, including
the 6 Build 1–2 tools the fix groups and the 3 new feel
tools. Registry moves 575 -> 578, then 578 -> 580 with Wave 8 Build 5. Nothing here writes a
real file except the confirm-gated report tool under a
registered handler, into temp dirs.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import math
import tempfile
import unittest
from pathlib import Path

from core import feel as feel_mod
from core import feel_tools as feel_tools
from core import confirm_gate
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)



def _catalogue_groups():
    """GROUP_MEMBERS from the single catalogue source
    (core/catalogue_groups_data.py — pure data, no Qt)."""
    # GAPS-23 (F-V7): the AST re-parse is gone with the Qt
    # module that forced it — the tables live in core now.
    from core.catalogue_groups_data import GROUP_MEMBERS
    return {name: group
            for group, names in GROUP_MEMBERS.items()
            for name in names}


def _good_hud(**overrides):
    descriptor = {
        "surface": "Globe page layers panel",
        "rows": [
            {"label": "Earthquakes", "action": "show",
             "value_kind": "measured", "value": "12 events today"},
            {"label": "Air quality", "action": "show",
             "value_kind": "unknown"},
        ],
        "state": "waiting",
        "accent": "amber",
        "language": "Waiting for you.",
    }
    descriptor.update(overrides)
    return descriptor


def _good_sound(**overrides):
    spec = {"name": "soft blip", "wave": "sine",
            "freq_start": 440, "freq_end": 880,
            "attack": 0.05, "sustain": 0.2, "decay": 0.1,
            "volume": 0.5}
    spec.update(overrides)
    return spec


def _good_feel(**overrides):
    feel = {"orb_level": 0.5, "hud": _good_hud(),
            "sound": _good_sound(), "particle_tier": "reduced",
            "hud_language": ["Ready.", "Waiting for you."],
            "font": "Orbitron"}
    feel.update(overrides)
    return feel


def _result(payload):
    """tools.execute returns {"status": "success", "result": <JSON str>}."""
    assert payload["status"] == "success", payload
    return json.loads(payload["result"])


class TestOrbMapping(unittest.TestCase):
    def test_endpoints(self):
        quiet = feel_mod.orb_map(0.0)
        self.assertTrue(quiet["ok"], quiet)
        self.assertEqual((quiet["scale"], quiet["glow"], quiet["pulse_hz"]),
                         (1.0, 0.25, 0.5))
        loud = feel_mod.orb_map(1.0)
        self.assertTrue(loud["ok"], loud)
        self.assertEqual((loud["scale"], loud["glow"], loud["pulse_hz"]),
                         (1.35, 1.0, 3.0))
        self.assertEqual(loud["accent"], feel_mod.BLUE_ACCENT)
        self.assertIn("OWED", loud["orb_licence"])

    def test_unmeasured_levels_refused(self):
        for bad in (-0.1, 1.1, float("nan"), float("inf"),
                    "0.5", True, None):
            result = feel_mod.orb_map(bad)
            self.assertFalse(result["ok"], (bad, result))


class TestHudRules(unittest.TestCase):
    def test_compliant_descriptor_passes(self):
        result = feel_mod.validate_hud(_good_hud())
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["colour"], feel_mod.AMBER)
        self.assertEqual(result["state"], "waiting")

    def test_colour_rule(self):
        # Red only for stop/failure; amber only for waiting/confirm.
        self.assertTrue(feel_mod.validate_hud(
            _good_hud(state="failure", accent="red"))["ok"])
        self.assertFalse(feel_mod.validate_hud(
            _good_hud(state="waiting", accent="red"))["ok"])
        self.assertFalse(feel_mod.validate_hud(
            _good_hud(state="active", accent="amber"))["ok"])
        self.assertFalse(feel_mod.validate_hud(
            _good_hud(state="idle", accent="purple"))["ok"])

    def test_honesty_reused_from_build_3(self):
        # Fake-data row: Build 3's measured-or-unknown rule refuses.
        bad = _good_hud()
        bad["rows"] = [{"label": "Earthquakes", "action": "show",
                        "value_kind": "estimated", "value": "~12"}]
        self.assertFalse(feel_mod.validate_hud(bad)["ok"])
        # Unknown row carrying a value: also refused by Build 3.
        bad = _good_hud()
        bad["rows"] = [{"label": "Air quality", "action": "show",
                        "value_kind": "unknown", "value": "42"}]
        self.assertFalse(feel_mod.validate_hud(bad)["ok"])

    def test_face_and_veto_refused(self):
        bad = _good_hud()
        bad["rows"] = [{"label": "face panel", "action": "show",
                        "value_kind": "unknown"}]
        self.assertFalse(feel_mod.validate_hud(bad)["ok"])
        bad = _good_hud(surface="earcon panel")
        self.assertFalse(feel_mod.validate_hud(bad)["ok"])
        bad = _good_hud(surface="Space Grotesk HUD")
        self.assertFalse(feel_mod.validate_hud(bad)["ok"])
        bad = _good_hud(language="I am JARVIS, at your service.")
        self.assertFalse(feel_mod.validate_hud(bad)["ok"])

    def test_language_must_be_own_copy(self):
        self.assertFalse(feel_mod.validate_hud(
            _good_hud(language="Hello, gorgeous."))["ok"])


class TestSoundsAndPacks(unittest.TestCase):
    def test_render_is_deterministic_and_bounded(self):
        first = feel_mod.render_sound_wav(_good_sound())
        second = feel_mod.render_sound_wav(_good_sound())
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["sample_count"], int(0.35 * 22050))
        self.assertEqual(first["wav_sha256"], second["wav_sha256"])
        self.assertLessEqual(first["peak"], int(0.5 * 32767))

    def test_bad_specs_refused(self):
        for spec in (_good_sound(wave="flute"),
                     _good_sound(freq_start=10),
                     _good_sound(attack=0, sustain=0, decay=0),
                     _good_sound(attack=2, sustain=2, decay=2),
                     _good_sound(volume=1.5)):
            self.assertFalse(feel_mod.validate_sound_spec(spec)["ok"], spec)

    def test_pack_without_primary_read_refused(self):
        pack = {"name": "bleeps", "licence": "CC-BY-4.0",
                "source": "https://example.invalid/pack",
                "licence_text": "CC BY 4.0 licence text"}
        self.assertFalse(feel_mod.register_sound_pack(pack)["ok"])
        pack["primary_read"] = True
        result = feel_mod.register_sound_pack(pack)
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["bundled"])
        # Copyleft / non-commercial labels refused even with a read.
        for licence in ("GPL-3.0", "CC-BY-NC-4.0", "unknown-licence"):
            pack2 = dict(pack, licence=licence)
            self.assertFalse(
                feel_mod.register_sound_pack(pack2)["ok"], licence)


class TestFontAndParticles(unittest.TestCase):
    def test_stack_families_landed_bundled(self):
        # B-R1-7: each stack family is primary-read and bundled.
        # B-R1-7r: the body face is Sora, not Rajdhani.
        for family in ("Orbitron", "Sora", "Share Tech Mono"):
            status = feel_mod.font_status(family)
            self.assertTrue(status["ok"], status)
            self.assertEqual(status["status"], "LANDED")
            self.assertTrue(status["bundled"])

    def test_no_family_cleared_by_analogy(self):
        self.assertFalse(feel_mod.font_status("Inter")["ok"])
        self.assertFalse(feel_mod.font_status("Space Grotesk")["ok"])
        self.assertFalse(feel_mod.font_status("Chakra Petch")["ok"])
        self.assertFalse(feel_mod.font_status("JetBrains Mono")["ok"])

    def test_particle_budget_and_degradation(self):
        full = feel_mod.particle_budget("full")
        self.assertEqual(full["max_particles"], 400)
        self.assertEqual(full["degrades_to"], "reduced")
        self.assertEqual(feel_mod.particle_budget("off")["max_particles"], 0)
        self.assertFalse(feel_mod.particle_budget("ultra")["ok"])


class TestWiring(unittest.TestCase):
    def setUp(self):
        confirm_gate.register_handler(None)

    def tearDown(self):
        confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in ("feel_validate", "feel_orb_map", "feel_report"):
            self.assertIn(name, names)
        for name in ("feel_validate", "feel_orb_map"):
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("feel_report", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["feel_report"], ("output_path",))
        # Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638 registered, 634 -> 636 grouped).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 764)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_catalogue_fix_groups_build1_and_build2_tools(self):
        groups = _catalogue_groups()
        self.assertTrue(groups, "GROUP_MEMBERS not found in source")
        for name in ("antic_suggest_cards", "antic_grant_status",
                     "antic_cards_report", "release_checklist",
                     "installer_validate", "release_checklist_report"):
            self.assertIn(name, groups, name)
            self.assertEqual(groups[name], "System & apps", name)

    def test_catalogue_groups_every_tool_except_plugins(self):
        groups = _catalogue_groups()
        names = [d["name"] for d in tools.get_declarations()]
        missing = [n for n in names if n not in groups]
        # Before the fix, 8 were missing (6 fix tools + 2 plugins);
        # after it, only the 2 plugins the catalogue never groups —
        # plus click_anywhere (2026-11-14), which is deliberately
        # ungrouped: a hands-driver tool with playbook opt-out (M-24),
        # not a user-facing catalogue skill; prover C1 pins grouped 752.
        self.assertEqual(missing,
                         ["click_anywhere", "get_crypto_price",
                          "get_stock_quote"], missing)
        grouped = len(names) - len(missing)
        # First-open opt-in added 4 grouped continuous tools
        # (578 -> 582 grouped, 580 -> 584 registered). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613).
        # Research wiring batch: INTENTIONAL — deep_research grouped in Mind (638 -> 639).
        # Libwiring sweep: INTENTIONAL — 4 tools grouped (639 -> 643): teach trio in Mind, wallpaper_state in Network & display; §7 consumers batch 1: INTENTIONAL — 4 tools grouped (643 -> 647); §7 consumers batch 2: INTENTIONAL — 3 tools grouped (647 -> 650): drag_drop_action, monitor_action, journal_action.; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool grouped (651 -> 652): wallpaper_apply in Network & display; wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool grouped (652 -> 653): wallpaper_revert in Network & display
        self.assertEqual(grouped, 761, (len(names), grouped))  # INTENTIONAL — 675 -> 678: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10) grouped in Mind (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 678 -> 679 — summarise_text grouped in Files & media; R8-4 screen-break port (2026-11-10): 679 -> 682 — eye_rest_timer, breathing_guide, log_glass grouped in Everyday skills; R8-5 magic-wormhole port (2026-11-10): 682 -> 685 — wormhole_offer, wormhole_receive, wormhole_status grouped in Files & media; R8-6 yasb port (2026-11-10): 685 -> 686 — status_strip grouped in System & apps; R8-7 basic-pitch port (2026-11-10): 686 -> 688 — audio_to_midi + audio_to_midi_status grouped in Play & create; R8-8 mftool port (2026-11-10): 688 -> 690 — fund_returns, mutual_fund_nav grouped in Everyday skills; R8-9 pypresence adoption (2026-11-10): 690 -> 692 — discord_presence + set_discord_client_id grouped in System & apps; R9-2 rustic adoption (2026-11-10): 692 -> 697 — init_backup_repo + backup_files + list_backup_snapshots + restore_backup + backup_status grouped in Files & media; R9-3 whoosh port (2026-11-10): 697 -> 700 — index_files, search_indexed_files, disk_index_status grouped in Files & media; R9-4 mutagen port (2026-11-10): 700 -> 702 — sync_folders_two_way, two_way_sync_status grouped in Files & media; R9-8 elodie port (2026-11-10): 702 -> 704 — organise_photos, preview_photo_organisation grouped in Files & media; R10-1 chrome-devtools-mcp port (2026-11-10): 704 -> 707 — browser_console, browser_network, browser_performance grouped in Web; R10-3 browser-history port (2026-11-10): 707 -> 709 — browser_history, browser_bookmarks grouped in Web; R10-4 python-adblock port (2026-11-10): 709 -> 711 — check_url_blocked, filter_list_status grouped in Web; R10-5 python-readability port (2026-11-10): 711 -> 712 — extract_article grouped in Web; R11-1 ollama port (2026-11-10): 712 -> 715 — ollama_status, ollama_pull, ollama_delete grouped in System & apps; R16-8 event-planning pack (2026-11-11): 715 -> 721 — six tools grouped in Everyday skills; R16-9 attic pack (2026-11-11): 721 -> 722 — home_inventory grouped in Everyday skills; R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        for name in ("feel_validate", "feel_orb_map", "feel_report"):
            self.assertEqual(groups[name], "System & apps", name)

    def test_read_only_tools_run_without_handler(self):
        result = _result(tools.execute(
            "feel_validate", {"feel_json": json.dumps(_good_feel())}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["font"]["status"], "LANDED")
        result = _result(tools.execute("feel_orb_map", {"level": 0.25}))
        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(result["scale"], 1.0875)

    def test_confirm_tool_refused_without_handler(self):
        result = tools.execute(
            "feel_report",
            {"feel_json": json.dumps(_good_feel()),
             "output_path": "/tmp/feel_no_handler.json"})
        # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
        self.assertEqual(result.get("status"), "refused")
        self.assertEqual(result.get("kind"), "not_confirmed")

    def test_report_tool_re_validates_and_writes_only_report(self):
        confirm_gate.register_handler(lambda *a: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "report.json"
                result = _result(tools.execute(
                    "feel_report",
                    {"feel_json": json.dumps(_good_feel()),
                     "output_path": str(target)}))
                self.assertTrue(result["ok"], result)
                on_disk = json.loads(target.read_text())
                self.assertEqual(on_disk["kind"], "FEEL_VALIDATION")
                self.assertEqual(on_disk["licences"]["font_orbitron"],
                                 "LANDED (OFL 1.1, primary-read B-R1-7)")
                # A refused feel writes nothing, even with a handler.
                bad = _good_feel(hud=_good_hud(state="waiting",
                                               accent="red"))
                result = _result(tools.execute(
                    "feel_report",
                    {"feel_json": json.dumps(bad),
                     "output_path": str(target)}))
                self.assertFalse(result["ok"])
                # A missing parent folder is refused, never created.
                missing = Path(tmp) / "nope" / "report.json"
                result = _result(tools.execute(
                    "feel_report",
                    {"feel_json": json.dumps(_good_feel()),
                     "output_path": str(missing)}))
                self.assertFalse(result["ok"])
                self.assertFalse(missing.parent.exists())
        finally:
            confirm_gate.register_handler(None)

    def test_module_has_no_execution_or_asset_surface(self):
        source = Path(feel_mod.__file__).read_text()
        tree = ast.parse(source)
        banned = {"deploy", "apply_proposal", "promote", "install",
                  "self_deploy", "execute", "send_payment",
                  "make_payment", "transfer", "open"}
        found = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in banned:
                found.add(node.name)
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in ("subprocess", "socket", "requests"):
                        found.add(alias.name)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("subprocess", "socket", "requests"):
                        found.add(alias.name)
        self.assertEqual(found, set(), found)
        # B-R1-7 added the OFL font bundle (no .wav, no sample):
        # the stack files exist, and no FEEL_SOUNDS dir was added.
        assets = Path(feel_mod.__file__).resolve().parent.parent / "gui" / "assets"
        self.assertTrue((assets / "fonts" / "Orbitron-Variable.ttf").exists())
        self.assertTrue((assets / "fonts" / "Sora-Variable.ttf").exists())
        self.assertTrue((assets / "fonts" / "ShareTechMono-Regular.ttf").exists())
        self.assertFalse((assets / "FEEL_SOUNDS").exists())


if __name__ == "__main__":
    unittest.main()


# L3: budget-isolation guest rule (test-isolation precedent,
# shared helper tests/_budget_isolation.py) — this suite
# executes tools through the shared funnel, so it snapshots
# the budget window + clock + confirm handler, opens a clean
# window, and restores byte-for-byte, instead of inheriting
# the earlier suites' dispatches and tripping on ordering.
_BUDGET_SNAPSHOT = None


def setUpModule():
    global _BUDGET_SNAPSHOT
    _BUDGET_SNAPSHOT = snapshot_budget()


def tearDownModule():
    global _BUDGET_SNAPSHOT
    if _BUDGET_SNAPSHOT is not None:
        restore_budget(_BUDGET_SNAPSHOT)
        _BUDGET_SNAPSHOT = None
