"""Triage Wire Build 2 — Creative/Developer/Social candidates #4-#7
(creative_rights_ledger / creative_subtitle_edit / developer_codebase_qa / social_vcard)
wired as live tools via core/creative_dev_social_t2.py (leaves
unmodified). Tests run against tmp dirs / tmp ledger files
wherever a file is written, except the registry-gate tests,
which use the real registry funnel (its audit drift is restored
by the build's data restore, like every other suite). No media
is touched, no renderer is driven, no code is imported from a
scanned codebase, and no vobject is used anywhere: the vCard
tools are stdlib-native by construction.

Field proof owed after the BB101 quiz (a real export refused
on a missing licence, a real retimed subtitle he can play, a
real codebase answer checked against its files, a real contact
round-tripping in his contacts) is not claimed here.
"""

import json
import os
import tempfile
import unittest

from core import creative_dev_social_t2 as flow
from core.funnel import tools_registry as registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

try:
    from _budget_isolation import restore_budget, snapshot_budget
except ImportError:  # run as a package module instead of top-level
    from tests._budget_isolation import (  # type: ignore
        restore_budget, snapshot_budget)



def _ledger(tmp):
    return os.path.join(tmp, "rights.db")


_SRT = ("1\n00:00:01,000 --> 00:00:04,000\nHello world\n\n"
        "2\n00:00:05,500 --> 00:00:08,000\nSecond cue\n")
_VTT = ("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nHello world\n\n"
        "00:00:05.500 --> 00:00:08.000\nSecond cue\n")
_VCARD = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\n"
          "N:Lovelace;Ada;;;\r\nTEL:+44 20 7946 0958\r\n"
          "EMAIL:ada@example.org\r\nEND:VCARD\r\n")


class LedgerTests(unittest.TestCase):
    def test_gate_allows_cleared_and_refuses_uncleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _ledger(tmp)
            cleared = flow.add_asset(
                ledger, "song.wav", "Licensor", "cc-by-4.0",
                attribution_required=False, standalone_sale_allowed=True)
            verdict = flow.check_rights(ledger, cleared)
            self.assertEqual(verdict["verdict"], "ALLOW")
            self.assertEqual(flow.gate_export(ledger, cleared)["verdict"],
                             "ALLOW")
            # Unverified licence: never upgraded, BLOCK at export.
            uncleared = flow.add_asset(
                ledger, "loop.wav", "Licensor", "unverified")
            verdict = flow.check_rights(ledger, uncleared)
            self.assertEqual(verdict["verdict"], "BLOCK")
            with self.assertRaises(ValueError):
                flow.gate_export(ledger, uncleared)
            # Non-commercial licence in a commercial export: BLOCK.
            nc = flow.add_asset(ledger, "nc.wav", "Licensor",
                                "cc-by-nc-4.0")
            self.assertEqual(flow.check_rights(ledger, nc)["verdict"],
                             "BLOCK")
            # Missing asset: BLOCK, never a guess.
            self.assertEqual(flow.check_rights(ledger, 9999)["verdict"],
                             "BLOCK")

    def test_gate_warn_is_not_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _ledger(tmp)
            asset = flow.add_asset(
                ledger, "old.wav", "Licensor", "cc-by-4.0",
                attribution_required=True, attribution_text=None,
                standalone_sale_allowed=True)
            verdict = flow.check_rights(ledger, asset)
            self.assertEqual(verdict["verdict"], "WARN")
            with self.assertRaises(ValueError):
                flow.gate_export(ledger, asset)

    def test_generated_free_tier_defaults_non_commercial(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _ledger(tmp)
            asset = flow.record_generated_asset(
                ledger, "gen.wav", "SomeGen", "free")
            self.assertEqual(flow.check_rights(ledger, asset)["verdict"],
                             "BLOCK")

    def test_ledger_never_lives_in_assistant_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                flow.add_asset(os.path.join(tmp, "assistant.db"),
                               "x.wav", "Licensor", "cc-by-4.0")

    def test_check_handler_reports_cleared_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = _ledger(tmp)
            asset = flow.add_asset(
                ledger, "ok.wav", "Licensor", "mit",
                standalone_sale_allowed=True)
            result = json.loads(flow.creative_rights_check_handler(
                ledger, asset))
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["cleared"])
            result = json.loads(flow.creative_rights_check_handler(
                ledger, 9999))
            self.assertEqual(result["status"], "ok")
            self.assertFalse(result["cleared"])
            self.assertEqual(result["verdict"]["verdict"], "BLOCK")


class SubtitleTests(unittest.TestCase):
    def test_srt_retime_preserves_cues_and_text(self):
        result = flow.edit_subtitles(_SRT, shift=2.0)
        self.assertEqual(len(result["cues"]), 2)
        self.assertEqual(result["cues"][0]["text"], "Hello world")
        self.assertEqual(result["cues"][1]["text"], "Second cue")
        self.assertAlmostEqual(result["cues"][0]["start"], 3.0)
        self.assertAlmostEqual(result["cues"][1]["end"], 10.0)
        self.assertIn("00:00:03,000 --> 00:00:06,000", result["text"])

    def test_vtt_parses_and_round_trips(self):
        result = flow.edit_subtitles(_VTT, output_format="vtt")
        self.assertEqual(len(result["cues"]), 2)
        self.assertTrue(result["text"].startswith("WEBVTT"))
        self.assertIn("00:00:01.000 --> 00:00:04.000", result["text"])

    def test_malformed_block_raises_and_drops_nothing_silently(self):
        with self.assertRaises(ValueError):
            flow.edit_subtitles("1\nNo time line here\nHello\n")

    def test_scale_and_sync(self):
        result = flow.edit_subtitles(_SRT, scale=2.0)
        self.assertAlmostEqual(result["cues"][0]["start"], 2.0)
        result = flow.edit_subtitles(_SRT, sync_first_start=10.0)
        self.assertAlmostEqual(result["cues"][0]["start"], 10.0)
        # The gap between cues is preserved by a sync shift.
        self.assertAlmostEqual(result["cues"][1]["start"]
                               - result["cues"][0]["start"], 4.5)

    def test_export_writes_file_and_refuses_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.srt")
            result = flow.write_subtitles(_SRT, out, shift=1.0)
            self.assertTrue(os.path.exists(out))
            self.assertIn("00:00:02,000", result["text"])
            with self.assertRaises(ValueError):
                flow.write_subtitles(_SRT,
                                     os.path.join(tmp, "nope", "o.srt"))


class TimelineTests(unittest.TestCase):
    def test_timeline_exports_otio_with_clips(self):
        tracks = json.dumps([{"name": "V1", "clips": [
            {"id": "a", "source": "a.mp4", "start": 0, "duration": 5},
            {"id": "b", "source": "b.mp4", "start": 5, "duration": 3,
             "transition": {"type": "dissolve", "duration": 0.5}}]}])
        otio = json.loads(flow.build_timeline("Test", fps=30,
                                              tracks_json=tracks))
        self.assertEqual(otio["OTIO_SCHEMA"], "Timeline.1")
        children = otio["tracks"][0]["children"]
        self.assertEqual(len(children), 2)
        self.assertEqual(children[0]["name"], "a")
        self.assertEqual(children[1]["metadata"]["transition"]["type"],
                         "dissolve")
        # No media is touched: the spine carries sources only.
        self.assertEqual(children[0]["metadata"]["source"], "a.mp4")

    def test_timeline_refuses_negative_clip(self):
        tracks = json.dumps([{"name": "V1", "clips": [
            {"id": "a", "source": "a.mp4", "start": -1,
             "duration": 5}]}])
        with self.assertRaises(ValueError):
            flow.build_timeline("Bad", tracks_json=tracks)


class CodebaseTests(unittest.TestCase):
    def _repo(self, tmp):
        os.makedirs(os.path.join(tmp, "pkg"))
        with open(os.path.join(tmp, "pkg", "mod.py"), "w") as f:
            f.write("def greet(name):\n    return 'hi ' + name\n\n\n"
                    "def main():\n    print(greet('ada'))\n")
        return tmp

    def test_answer_carries_real_file_line_citations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            result = flow.answer_codebase_question(
                root, "Where is greet defined and called?")
            self.assertTrue(result["cited"])
            # greet is defined on line 1 of pkg/mod.py.
            self.assertTrue(any(c.endswith("mod.py:1")
                                for c in result["citations"]))
            # The call site (line 6) is cited too.
            self.assertTrue(any(c.endswith("mod.py:6")
                                for c in result["citations"]))

    def test_uncited_question_is_refused_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._repo(tmp)
            result = flow.answer_codebase_question(
                root, "Where is frobnicate_the_wobble defined?")
            self.assertFalse(result["cited"])
            self.assertEqual(result["answer"], "")
            handler = json.loads(flow.dev_codebase_qa_handler(
                root, "Where is frobnicate_the_wobble defined?"))
            self.assertEqual(handler["status"], "error")

    def test_missing_root_is_an_error(self):
        with self.assertRaises(ValueError):
            flow.answer_codebase_question("/definitely/not/a/dir",
                                          "Where is greet?")


class VCardTests(unittest.TestCase):
    def test_import_maps_name_phones_emails(self):
        result = flow.import_vcards(_VCARD)
        self.assertEqual(result["cards"], 1)
        person = result["people"][0]
        self.assertEqual(person["name"], "Ada Lovelace")
        self.assertEqual(person["phones"], ["+44 20 7946 0958"])
        self.assertEqual(person["emails"], ["ada@example.org"])

    def test_import_nameless_card_is_refused(self):
        bad = ("BEGIN:VCARD\r\nVERSION:3.0\r\nTEL:123\r\nEND:VCARD\r\n")
        with self.assertRaises(ValueError):
            flow.import_vcards(bad)

    def test_export_round_trips_name_phone_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "ada.vcf")
            contacts = json.dumps([{
                "name": "Ada Lovelace",
                "phones": ["+44 20 7946 0958"],
                "emails": ["ada@example.org"],
                "note": "Pioneering programmer"}])
            result = flow.export_vcards(contacts, out)
            self.assertTrue(os.path.exists(out))
            self.assertEqual(result["cards"], 1)
            with open(out, encoding="utf-8") as f:
                back = flow.import_vcards(f.read())
            person = back["people"][0]
            self.assertEqual(person["name"], "Ada Lovelace")
            self.assertEqual(person["phones"], ["+44 20 7946 0958"])
            self.assertEqual(person["emails"], ["ada@example.org"])

    def test_export_refuses_missing_folder(self):
        with self.assertRaises(ValueError):
            flow.export_vcards(json.dumps([{"name": "Ada"}]),
                               "/definitely/not/a/dir/a.vcf")


class RegistryTests(unittest.TestCase):
    def test_new_tools_registered_and_classified(self):
        decls = registry_mod.tools.get_declarations()
        names = {d["name"] for d in decls}
        for name in ("creative_rights_check", "creative_rights_add",
                     "creative_subtitle_edit",
                     "creative_subtitle_export", "creative_timeline",
                     "dev_codebase_qa", "vcard_import", "vcard_export"):
            self.assertIn(name, names)
        # Triage Wire Build 3 added 7 tools (506 -> 513); Triage
        # Wire Build 4 added 19 tools (513 -> 532: 5 finance
        # playbook + 14 social ledger); Wave 6 integration
        # added 19 tools (532 -> 551: 7 harness + 3 WORK +
        # 3 MISS + 6 mission); Wave 7 Build 1 added 2
        # Wave 7 Build 2 added 3 undo tools (553 -> 556); sandbox tools (551 -> 553: sandbox_run +
        # Wave 7 Build 3 added 2 SIM tools (556 -> 558); Wave 7 Build 4 added 2 proposal tools (558 -> 560); Wave 7 Build 5 added 3 multiverse tools (560 -> 563); Wave 8 Build 1 added 3 anticipation tools (563 -> 566); Wave 8 Build 2 added 3 Track L tools (566 -> 575); Wave 8 Build 3 added 6 Phase 5 tools (569 -> 575); Wave 8 Build 4 added 3 feel tools (575 -> 578); Wave 8 Build 5 added 2 learning-last tools (578 -> 580; first-open opt-in added 4 continuous tools (580 -> 584)). Wave 9 Build 1 added 5 wellbeing tools (584 -> 589). Wave 9 Build 2 added 4 communications tools (589 -> 593). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 3 added 4 local IoT tools (593 -> 597). Wave 9 Build 4 added 4 hardware tools (597 -> 601). Wave 9 Build 5 added 4 accessibility tools (601 -> 605). Wave 9 Build 6 adds 4 network tools (605 -> 609). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 7 adds 4 gaming tools (609 -> 613). Wave 9 Build 8 adds 7 pilot tools (613 -> 620). Wave 9 Build 9 adds 10 travel tools (620 -> 630). Wave 9 Build 10 adds 2 graduation tools (630 -> 632). Wave 10 Build 1 adds 2 release-spine tools (632 -> 634). Wave 10 Build 2 adds 2 trust tools (634 -> 636). Wave 10 Build 3 adds 2 first-run tools (636 -> 638). Wave 10 Build 4 adds 2 conformance-evidence tools (638 -> 640).
        # sandbox_list); the count is the live
        # registry, not a magic number.
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Library sweep: INTENTIONAL — 4 tools added (641 -> 645); pin was stale since the sweep, proven identical (645 != 641) on the pristine backup tree; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; stale-pin sweep: INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply; wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(decls), 758)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_writers_are_confirm_gated_and_readers_are_not(self):
        from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/

        for name in ("creative_rights_add",
                     "creative_subtitle_export", "vcard_export"):
            self.assertIn(name, gate._CONFIRM_GATE_TOOLS)
        for name in ("creative_rights_check", "creative_subtitle_edit",
                     "creative_timeline", "dev_codebase_qa",
                     "vcard_import"):
            self.assertIn(name, gate._READ_ONLY_TOOLS)
            self.assertNotIn(name, gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(gate._WRITE_DESTINATION_ARGS[
            "creative_rights_add"], ("ledger_path",))
        self.assertEqual(gate._WRITE_DESTINATION_ARGS[
            "creative_subtitle_export"], ("output_path",))
        self.assertEqual(gate._WRITE_DESTINATION_ARGS[
            "vcard_export"], ("output_path",))

    def test_gated_writer_refuses_without_confirm_handler(self):
        # The registry funnel with no confirm handler refuses a
        # gated writer without writing anything (study precedent).
        from core import confirm_gate
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

        confirm_gate.clear_handler()
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out.srt")
            result = tools.execute(
                "creative_subtitle_export",
                {"subtitles_text": _SRT, "output_path": out})
            self.assertEqual(result.get("kind"), "not_confirmed")
            self.assertFalse(os.path.exists(out))

    def test_gated_writer_writes_with_confirm_handler(self):
        from core import confirm_gate
        from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

        confirm_gate.register_handler(lambda name, summary: True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "out.srt")
                result = tools.execute(
                    "creative_subtitle_export",
                    {"subtitles_text": _SRT, "output_path": out})
                self.assertTrue(os.path.exists(out))
                payload = json.loads(result["result"])
                self.assertEqual(payload["status"], "ok")
        finally:
            confirm_gate.clear_handler()


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
