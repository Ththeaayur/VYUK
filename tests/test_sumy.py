"""R8-3 sumy — extractive TextRank summarisation as VYUK's own
code (PORT, not adopt). Pins: the splitter (abbreviations,
decimals, quotes, newlines, dedup), the ranking (carrying
sentences beat the first lines, order preserved), the meeting
notes wiring (first-5-lines is gone, the meetily pin still
holds), the funnel tool, and the no-dependency boundary.
"""

from __future__ import annotations

import ast
import pathlib
import time
import unittest

from core.daily_meeting import MeetingGate, produce_meeting_notes
from core.summary import (
    extractive_summary,
    split_sentences,
    summarise_text,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent

TRANSCRIPT = """Hi everyone, thanks for joining the call.
Can you hear me okay? Yes, I think so.
Right, the agenda today is the launch date.
We need to decide the launch date before Friday.
The launch date is the one decision we cannot postpone.
Marketing needs six weeks of lead time after we lock the date.
Engineering says the payment flow is the only blocker left.
The payment flow fails when the card expires mid-checkout.
We agreed to fix the payment flow by Wednesday.
The launch date decision is due before Friday.
Sarah owns the payment flow fix.
Tom will confirm the marketing lead time tomorrow.
Okay, thanks everyone, talk tomorrow."""

CARRYING = [
    "We need to decide the launch date before Friday.",
    "The launch date is the one decision we cannot postpone.",
    "We agreed to fix the payment flow by Wednesday.",
    "The launch date decision is due before Friday.",
]
GREETING = [
    "Hi everyone, thanks for joining the call.",
    "Can you hear me okay? Yes, I think so.",
    "Right, the agenda today is the launch date.",
]


class TestSplitter(unittest.TestCase):
    def test_abbreviations_and_decimals_never_split(self):
        out = split_sentences(
            "Dr. Smith met Mr. Jones at 3.5 p.m. in the U.S. office. "
            "The budget was $2.5 million, e.g. for Q3. We ship Friday!")
        self.assertEqual(out, [
            "Dr. Smith met Mr. Jones at 3.5 p.m. in the U.S. office.",
            "The budget was $2.5 million, e.g. for Q3.",
            "We ship Friday!",
        ])

    def test_initials_never_split(self):
        self.assertEqual(
            split_sentences("J. R. R. Tolkien wrote the book. "
                            "It was long."),
            ["J. R. R. Tolkien wrote the book.", "It was long."])

    def test_quotes_kept_and_newlines_are_boundaries(self):
        self.assertEqual(
            split_sentences('"We ship Friday." She nodded. '
                            "Then everyone left."),
            ["We ship Friday.", "She nodded.", "Then everyone left."])
        self.assertEqual(
            split_sentences("No punctuation here\n"
                            "Second line no punctuation"),
            ["No punctuation here", "Second line no punctuation"])
        self.assertEqual(
            split_sentences("- First point\n- Second point"),
            ["First point", "Second point"])

    def test_duplicates_dropped_first_kept(self):
        self.assertEqual(
            split_sentences("Yeah. Yeah. Yeah. Something real "
                            "happened. Yeah."),
            ["Yeah.", "Something real happened."])

    def test_empty_and_single(self):
        self.assertEqual(split_sentences(""), [])
        self.assertEqual(split_sentences("   "), [])
        self.assertEqual(split_sentences("One sentence only"),
                         ["One sentence only"])


class TestTextRank(unittest.TestCase):
    def test_carrying_sentences_beat_the_first_lines(self):
        result = summarise_text(TRANSCRIPT)
        self.assertEqual(result["total_sentences"], 14)
        self.assertEqual(result["sentence_count"], 5)
        self.assertEqual(result["method"], "extractive-textrank")
        for sentence in CARRYING:
            self.assertIn(sentence, result["sentences"])
        for line in GREETING:
            self.assertNotIn(line, result["sentences"])

    def test_summary_is_not_the_first_five_lines(self):
        first_five = " ".join(TRANSCRIPT.splitlines()[:5])
        result = summarise_text(TRANSCRIPT)
        self.assertNotEqual(result["summary"], first_five)

    def test_chosen_sentences_stay_in_document_order(self):
        result = summarise_text(TRANSCRIPT)
        positions = [TRANSCRIPT.index(s) for s in result["sentences"]]
        self.assertEqual(positions, sorted(positions))

    def test_max_sentences_respected_and_clamped(self):
        self.assertEqual(
            len(summarise_text(TRANSCRIPT, max_sentences=2)["sentences"]),
            2)
        self.assertEqual(
            len(summarise_text(TRANSCRIPT, max_sentences=99)["sentences"]),
            14)
        self.assertEqual(
            len(summarise_text(TRANSCRIPT, max_sentences=0)["sentences"]),
            1)
        self.assertEqual(
            len(summarise_text(TRANSCRIPT,
                               max_sentences="nonsense")["sentences"]),
            5)

    def test_short_text_returns_whole(self):
        result = summarise_text("Hello world\nAction: ship it")
        self.assertEqual(result["summary"], "Hello world Action: ship it")
        self.assertEqual(result["total_sentences"], 2)

    def test_empty_is_honest(self):
        result = summarise_text("   ")
        self.assertEqual(result["summary"], "")
        self.assertEqual(result["sentence_count"], 0)
        self.assertEqual(result["total_sentences"], 0)

    def test_deterministic(self):
        self.assertEqual(summarise_text(TRANSCRIPT),
                         summarise_text(TRANSCRIPT))

    def test_long_transcript_stays_fast(self):
        words = ["launch", "payment", "meeting", "decision", "friday",
                 "team", "budget", "ship", "review", "plan"]
        import random
        random.seed(7)
        lines = []
        for _ in range(600):
            picked = random.sample(words, 4)
            lines.append(f"The {' '.join(picked)} is on track for the "
                         "launch date and the payment flow.")
        started = time.time()
        result = summarise_text("\n".join(lines), max_sentences=5)
        self.assertLess(time.time() - started, 10.0)
        self.assertEqual(result["sentence_count"], 5)


class TestMeetingNotesWiring(unittest.TestCase):
    def test_meetily_pin_still_holds(self):
        # R7-4 pin: a 2-line transcript summarises to itself.
        gate = MeetingGate(opt_in=True, indicator_on=True)
        notes, error = produce_meeting_notes(
            "hello world\nAction: ship it", gate)
        self.assertIsNone(error)
        self.assertEqual(notes["summary"], "hello world Action: ship it")
        self.assertEqual(notes["summary_method"], "extractive-textrank")
        self.assertEqual(notes["summary_sentences"], 2)

    def test_meeting_notes_summary_is_not_first_five_lines(self):
        gate = MeetingGate(opt_in=True, indicator_on=True)
        notes, error = produce_meeting_notes(TRANSCRIPT, gate)
        self.assertIsNone(error)
        first_five = " ".join(TRANSCRIPT.splitlines()[:5])
        self.assertNotEqual(notes["summary"], first_five)
        for sentence in CARRYING:
            self.assertIn(sentence, notes["summary"])
        self.assertEqual(notes["summary_method"], "extractive-textrank")
        self.assertEqual(notes["summary_sentences"], 5)
        # 13 lines, 14 sentences ("Can you hear me okay? Yes, I
        # think so." is two sentences on one line).
        self.assertEqual(notes["line_count"], 13)

    def test_gate_still_refuses_without_opt_in_or_indicator(self):
        notes, error = produce_meeting_notes(
            TRANSCRIPT, MeetingGate(opt_in=False, indicator_on=True))
        self.assertIsNone(notes)
        self.assertIn("not opted in", error)
        notes, error = produce_meeting_notes(
            TRANSCRIPT, MeetingGate(opt_in=True, indicator_on=False))
        self.assertIsNone(notes)
        self.assertIn("indicator is not on", error)


class TestFunnelTool(unittest.TestCase):
    def test_tool_declared_read_only_and_grouped(self):
        from core.funnel.tools_registry import tools as _registry
        from core.funnel.tools_gate import _READ_ONLY_TOOLS
        decls = {d["name"]: d for d in _registry.get_declarations()}
        self.assertIn("summarise_text", decls)
        self.assertEqual(
            sorted(decls["summarise_text"]["parameters"]["properties"]),
            ["max_sentences", "text"])
        self.assertIn("summarise_text", _READ_ONLY_TOOLS)
        # GROUP_MEMBERS via AST from the single catalogue source
        # (core/catalogue_groups_data.py, GAPS-23 F-V7 — same
        # shape as gen_catalogue_snapshot.py).
        import ast as _ast
        src = (ROOT / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        groups = None
        for node in _ast.walk(_ast.parse(src)):
            target = getattr(node, "target", None)
            if target is None and isinstance(node, _ast.Assign) \
                    and len(node.targets) == 1:
                target = node.targets[0]
            if target is not None and getattr(target, "id", "") \
                    == "GROUP_MEMBERS" and node.value is not None:
                groups = _ast.literal_eval(node.value)
                break
        self.assertIsNotNone(groups)
        self.assertIn("summarise_text", groups["Files & media"])

    def test_registry_total_is_764(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        from core.funnel.tools_registry import tools as _registry
        self.assertEqual(len(_registry.get_declarations()), 764)  # INTENTIONAL — 680 -> 681: summarise_text (R8-3 sumy port, 2026-11-10).; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_handler_returns_summary(self):
        # GAPS-23 (F-V7): the handler is funnel-only — a direct
        # call is refused — so the summary is asserted through
        # execute(), which returns the handler's plain string in
        # ["result"] (summarise_text is read-only: no confirm).
        from core.funnel.tools_registry import tools as _registry
        out = _registry.execute(
            "summarise_text", {"text": TRANSCRIPT})["result"]
        self.assertTrue(out.startswith("Summary (5 of 14 sentences):"))
        for sentence in CARRYING:
            self.assertIn(sentence, out)
        self.assertEqual(
            _registry.execute(
                "summarise_text", {"text": "   "})["result"],
            "There is nothing to summarise — give the text to summarise.")

    def test_no_network_imports_and_no_sumy_dependency(self):
        tree = ast.parse((ROOT / "core" / "summary.py")
                         .read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & {"socket", "urllib", "requests",
                                     "http", "nltk", "sumy"})
        requirements = (ROOT / "requirements.txt").read_text(
            encoding="utf-8")
        self.assertNotIn("sumy", requirements.lower())
        self.assertNotIn("nltk", requirements.lower())


if __name__ == "__main__":
    unittest.main()
