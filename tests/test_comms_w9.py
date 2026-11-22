"""Communications deltas tests — draft and confirm only,
nothing ever sent.

Proves: the draft echoes exactly caller-supplied
recipient, body, and channel, with sent False and status
DRAFT, and a note saying it is not sent; the recipient
check returns supplied or missing only, never guesses,
never merges, and never infers a relationship; the
send-readiness descriptor states the draft is not sent
and names the two missing steps (human confirmation and
a real send pipeline, neither provided here); a draft
record re-validates and refuses a tampered draft, or one
claiming sent; any send action, list recipient, inferred
recipient, missing body, or sent claim refuses whole; no
network, messaging, or mail code exists in the module
(AST on imports and send-shaped call names, not on
prose); tool classifications, grouping in GROUP_MEMBERS
with friendly plain-language names, and funnel confirm
refusal without a handler. Registry moves 589 -> 593.
Nothing here writes a real file except the confirm-gated
record tool under a registered handler, into temp dirs
only.

Stdlib unittest, no GUI, no network, no code execution.
Run from the tree root with PYTHONPATH=. (as the other
suites are run by tests/run_tests.py).
"""

import ast
import json
import tempfile
import unittest
from pathlib import Path

from core import comms as comms_mod
from core import comms_tools as comms_tools
from core import confirm_gate
from core.funnel import tools_gate as gate  # Phase 3 F1 INTENTIONAL: tools_gate moved to core/funnel/
from core.funnel.tools_registry import tools  # Phase 3 F3 INTENTIONAL: moved to core/funnel/


def _catalogue_groups():
    """GROUP_MEMBERS from the single catalogue source
    (core/catalogue_groups_data.py — pure data, no Qt)."""
    # GAPS-23 (F-V7): the AST re-parse is gone with the Qt
    # module that forced it — the tables live in core now.
    from core.catalogue_groups_data import GROUP_MEMBERS
    return {group: set(names)
            for group, names in GROUP_MEMBERS.items()}


def _friendly_names():
    source = Path(__file__).resolve().parent.parent.joinpath(
        "core", "tool_labels.py").read_text()
        # INTENTIONAL (refactor P1 step 5): friendly-names table moved verbatim to window_widgets_toolnames.py.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) \
                        and target.id == "TOOL_FRIENDLY_NAMES":
                    return {k.value: v.value
                            for k, v in zip(node.value.keys,
                                            node.value.values)
                            if isinstance(k, ast.Constant)}
    raise AssertionError("TOOL_FRIENDLY_NAMES not found")


def _result(executed):
    if isinstance(executed, dict) and "result" in executed:
        value = executed["result"]
        return json.loads(value) if isinstance(value, str) else value
    return executed


COMMS_TOOLS = ("comms_draft_message", "comms_check_recipient",
               "comms_send_readiness", "comms_record_draft")


class DraftTests(unittest.TestCase):
    def test_draft_echoes_caller_fields_only(self):
        draft = comms_mod.build_message_draft(
            "Priya", "Meet at four?", "chat")
        self.assertEqual(draft["kind"], "MESSAGE_DRAFT")
        self.assertEqual(draft["recipient"], "Priya")
        self.assertEqual(draft["body"], "Meet at four?")
        self.assertEqual(draft["channel"], "chat")
        self.assertFalse(draft["sent"])
        self.assertEqual(draft["status"], "DRAFT")
        self.assertIn("NOT been sent", draft["note"])
        self.assertIn("human", draft["note"])
        self.assertIn("pipeline", draft["note"])

    def test_draft_action_draft_accepted_others_refused(self):
        draft = comms_mod.build_message_draft(
            "Priya", "Hi", "chat", action="draft")
        self.assertEqual(draft["status"], "DRAFT")
        for action in ("send", "transmit", "deliver", "post",
                       "dispatch", "forward", "broadcast", "teleport"):
            with self.assertRaises(ValueError, msg=action):
                comms_mod.build_message_draft(
                    "Priya", "Hi", "chat", action=action)

    def test_bulk_recipient_refused_whole(self):
        for recipient in (["Priya", "Ravi"], ("Priya",),
                          {"name": "Priya"}):
            with self.assertRaises(ValueError, msg=str(recipient)):
                comms_mod.build_message_draft(
                    recipient, "Hi", "chat")

    def test_missing_or_empty_body_refused(self):
        for body in (None, "", "   ", 42):
            with self.assertRaises(ValueError, msg=str(body)):
                comms_mod.build_message_draft("Priya", body, "chat")

    def test_sent_claim_in_body_refused(self):
        for body in ("This was sent yesterday",
                     "Message sent, all good",
                     "It has been sent already"):
            with self.assertRaises(ValueError, msg=body):
                comms_mod.build_message_draft("Priya", body, "chat")

    def test_body_may_mention_send_as_content(self):
        draft = comms_mod.build_message_draft(
            "Priya", "Please send me the notes", "chat")
        self.assertEqual(draft["body"], "Please send me the notes")
        self.assertFalse(draft["sent"])


class RecipientCheckTests(unittest.TestCase):
    def test_supplied_and_missing(self):
        supplied = comms_mod.check_recipient(
            {"name": "Priya", "channel": "chat"})
        self.assertEqual(supplied["status"], "supplied")
        self.assertEqual(supplied["name"], "Priya")
        self.assertEqual(supplied["channel"], "chat")
        missing = comms_mod.check_recipient({"name": "Priya"})
        self.assertEqual(missing["status"], "missing")
        self.assertEqual(missing["missing"], ["channel"])
        both = comms_mod.check_recipient({})
        self.assertEqual(both["missing"], ["name", "channel"])

    def test_no_guessing_no_merging_no_relationship(self):
        result = comms_mod.check_recipient(
            {"name": "Priya", "channel": "chat",
             "relationship": "colleague", "other_name": "Ravi"})
        self.assertNotIn("relationship", result)
        self.assertNotIn("other_name", result)
        self.assertNotIn("Ravi", json.dumps(result))

    def test_inferred_or_partial_refused_whole(self):
        for record in ({"name": "Priya", "channel": "chat",
                        "inferred": True},
                       {"name": "Pri", "channel": "chat",
                        "partial": True}):
            with self.assertRaises(ValueError, msg=str(record)):
                comms_mod.check_recipient(record)

    def test_list_record_refused_as_bulk(self):
        with self.assertRaises(ValueError):
            comms_mod.check_recipient([{"name": "Priya",
                                        "channel": "chat"}])


class ReadinessTests(unittest.TestCase):
    def _draft(self):
        return comms_mod.build_message_draft(
            "Priya", "Meet at four?", "chat")

    def test_readiness_states_not_sent_and_missing_steps(self):
        readiness = comms_mod.describe_send_readiness(self._draft())
        self.assertEqual(readiness["kind"], "SEND_READINESS")
        self.assertFalse(readiness["sent"])
        self.assertFalse(readiness["ready"])
        self.assertIn("a human confirmation of this exact draft",
                      readiness["missing_steps"])
        self.assertIn("a real send pipeline (none exists in this build)",
                      readiness["missing_steps"])

    def test_readiness_refuses_sent_or_wrong_shape(self):
        draft = self._draft()
        sent = dict(draft, sent=True)
        with self.assertRaises(ValueError):
            comms_mod.describe_send_readiness(sent)
        wrong_status = dict(draft, status="SENT")
        with self.assertRaises(ValueError):
            comms_mod.describe_send_readiness(wrong_status)
        # Readiness assesses the draft presented to it: a
        # body carrying a sent claim refuses, and a draft
        # missing a field refuses. It does not claim to
        # detect alteration from some other draft — that
        # is the record tool's job, which writes only the
        # module-rebuilt draft.
        sent_claim = dict(draft, body="This was sent yesterday")
        with self.assertRaises(ValueError):
            comms_mod.describe_send_readiness(sent_claim)
        missing_field = dict(draft)
        del missing_field["body"]
        with self.assertRaises(ValueError):
            comms_mod.describe_send_readiness(missing_field)
        with self.assertRaises(ValueError):
            comms_mod.describe_send_readiness("not a draft")


class RecordValidationTests(unittest.TestCase):
    def test_rebuild_ok_and_tampered_refused(self):
        rebuilt = comms_mod.validate_recordable_draft(
            {"recipient": "Priya", "body": "Hi", "channel": "chat"})
        self.assertEqual(rebuilt["recipient"], "Priya")
        self.assertFalse(rebuilt["sent"])
        for fields in ({"recipient": "Priya", "body": "Hi",
                        "channel": "chat", "sent": True},
                       {"recipient": "Priya", "body": "Hi",
                        "channel": "chat", "health_score": 9},
                       {"recipient": "Priya", "channel": "chat"},
                       {"recipient": "Priya", "body": "Hi",
                        "channel": "chat", "action": "send"}):
            with self.assertRaises(ValueError, msg=str(fields)):
                comms_mod.validate_recordable_draft(fields)


class NoNetworkTests(unittest.TestCase):
    def test_no_network_messaging_or_mail_code(self):
        source = Path(__file__).resolve().parent.parent.joinpath(
            "core", "comms.py").read_text()
        tree = ast.parse(source)
        banned_imports = {
            "socket", "requests", "urllib", "http", "smtplib",
            "imaplib", "poplib", "email", "aiohttp", "websockets",
            "ssl", "ftplib", "telnetlib",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0],
                                     banned_imports, alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0],
                                 banned_imports, node.module)
            if isinstance(node, ast.Call):
                func = node.func
                called = None
                if isinstance(func, ast.Name):
                    called = func.id
                elif isinstance(func, ast.Attribute):
                    called = func.attr
                self.assertNotIn(called, {"send", "transmit",
                                          "deliver", "post"},
                                 f"send-shaped call {called} in module")
        tools_source = Path(__file__).resolve().parent.parent.joinpath(
            "core", "comms_tools.py").read_text()
        tools_tree = ast.parse(tools_source)
        for node in ast.walk(tools_tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0],
                                     banned_imports, alias.name)
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0],
                                 banned_imports, node.module)


class WiringTests(unittest.TestCase):
    def setUp(self):
        self._confirm_gate = confirm_gate
        self._confirm_gate.register_handler(None)

    def tearDown(self):
        self._confirm_gate.register_handler(None)

    def test_tools_registered_with_classifications(self):
        names = {d["name"] for d in tools.get_declarations()}
        for name in COMMS_TOOLS:
            self.assertIn(name, names)
        for name in COMMS_TOOLS[:3]:
            self.assertIn(name, gate._READ_ONLY_TOOLS)
        self.assertIn("comms_record_draft", gate._CONFIRM_GATE_TOOLS)
        self.assertEqual(
            gate._WRITE_DESTINATION_ARGS["comms_record_draft"],
            ("output_path",))
        # Wave 9 Build 2 adds 4 communications tools (589 -> 593).
        # Research wiring batch: INTENTIONAL — deep_research tool added (640 -> 641).
        # Libwiring sweep: INTENTIONAL — 4 tools added (641 -> 645): teach_record_sequence, teach_list_sequences, teach_replay_sequence, wallpaper_state; §7 consumers batch 1: INTENTIONAL — 4 tools added (645 -> 649): menu_action, file_dialog_action, system_surface_action, adapter_action; §7 consumers batch 2: INTENTIONAL — 3 tools added (649 -> 652): drag_drop_action, monitor_action, journal_action; §7 consumers batch 8: INTENTIONAL — 1 tool added (652 -> 653): context_menu_action; wallpaper-apply batch (2026-11-03): INTENTIONAL — 1 tool added (653 -> 654): wallpaper_apply (confirm-gated; wallpaper_state unchanged, still read-only); wallpaper-revert batch (2026-11-05): INTENTIONAL — 1 tool added (654 -> 655): wallpaper_revert
        self.assertEqual(len(tools.get_declarations()), 764)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_tools_grouped_with_plain_language_names(self):
        groups = _catalogue_groups()
        grouped = set().union(*groups.values()) if groups else set()
        for name in COMMS_TOOLS:
            self.assertIn(name, grouped, name)
            self.assertIn(name, groups.get("System & apps", set()), name)
        names = _friendly_names()
        self.assertEqual(names["comms_draft_message"], "Draft message")
        self.assertEqual(names["comms_check_recipient"],
                         "Check recipient")
        self.assertEqual(names["comms_send_readiness"],
                         "Send readiness")
        self.assertEqual(names["comms_record_draft"], "Record draft")

    def test_readonly_tools_through_funnel(self):
        draft = _result(tools.execute("comms_draft_message", {
            "recipient": "Priya", "body": "Meet at four?",
            "channel": "chat"}))
        self.assertEqual(draft["kind"], "MESSAGE_DRAFT")
        self.assertFalse(draft["sent"])
        check = _result(tools.execute("comms_check_recipient", {
            "contact_json": json.dumps({"name": "Priya",
                                        "channel": "chat"})}))
        self.assertEqual(check["status"], "supplied")
        readiness = _result(tools.execute("comms_send_readiness", {
            "draft_json": json.dumps(draft)}))
        self.assertFalse(readiness["sent"])
        self.assertFalse(readiness["ready"])
        # A handler-level refusal flows through the funnel
        # as a success envelope carrying ok False — the
        # refusal itself is in the result, not the envelope.
        refused = _result(tools.execute("comms_draft_message", {
            "recipient": "Priya", "body": "Hi", "channel": "chat",
            "action": "send"}))
        self.assertFalse(refused["ok"], refused)
        self.assertIn("send action", refused["reason"], refused)

    def test_record_refused_without_confirm_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "draft.json")
            result = tools.execute("comms_record_draft", {
                "draft_json": json.dumps({"recipient": "Priya",
                                          "body": "Meet at four?",
                                          "channel": "chat"}),
                "output_path": target})
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(result["status"], "refused", result)
            self.assertEqual(result["kind"], "not_confirmed", result)
            self.assertFalse(Path(target).exists())

    def test_record_with_confirm_writes_record_only(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "draft.json")
            data = _result(tools.execute("comms_record_draft", {
                "draft_json": json.dumps({"recipient": "Priya",
                                          "body": "Meet at four?",
                                          "channel": "chat"}),
                "output_path": target}))
            self.assertTrue(data["ok"], data)
            self.assertFalse(data["executed"])
            self.assertFalse(data["sent"])
            written = json.loads(Path(target).read_text())
            self.assertEqual(written["draft"]["recipient"], "Priya")
            self.assertFalse(written["sent"])
            self.assertEqual(sorted(Path(tmp).iterdir()), [Path(target)])

    def test_record_refuses_tampered_or_sent_draft(self):
        self._confirm_gate.register_handler(lambda name, summary: True)
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "draft.json")
            for fields in ({"recipient": "Priya", "body": "Hi",
                            "channel": "chat", "sent": True},
                           {"recipient": "Priya", "body": "Hi",
                            "channel": "chat", "extra": "x"}):
                data = _result(tools.execute("comms_record_draft", {
                    "draft_json": json.dumps(fields),
                    "output_path": target}))
                self.assertFalse(data["ok"], data)
            self.assertFalse(Path(target).exists())


if __name__ == "__main__":
    unittest.main()
