"""Suite for R19-4 (nexmoe/VidBee inspiration): media_chat — chat
with your media. Q&A and summary over a transcript, answered by the
product's own Gemini key from the transcript alone. No downloader,
no speaker diarization, no video processing."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core import media_chat as mc
from core import audio_localise as al
from core import prompt_tune as pt
from core.catalogue_snapshot import GROUP_MEMBERS
from core.funnel.tools_gate import _needs_confirmation
from core.funnel.tools_gate_readclass import _is_read_class
from core.funnel.tools_registry import tools as _registry

TREE = Path(__file__).resolve().parents[1]
FAKE_KEY = "fake-gemini-key-for-tests-only"

SRT_TEXT = """1
00:00:01,000 --> 00:00:04,000
Welcome to the lecture on tides.

2
00:00:05,500 --> 00:00:09,000
The moon pulls the oceans twice a day.
"""

VTT_TEXT = """WEBVTT

00:00:01.000 --> 00:00:04.000
Welcome to the lecture on tides.

00:00:05.500 --> 00:00:09.000
The moon pulls the oceans twice a day.
"""

PLAIN_TEXT = "The moon pulls the oceans twice a day.\nTides follow the moon."


class _FakeOpener:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        body = json.dumps({
            "candidates": [{"content": {"parts": [
                {"text": self.answer}]}}],
        }).encode("utf-8")

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return body

        return _Resp()


class _FakeAlStore:
    """A stand-in for audio_localise._AlStore with one project and
    two segments, so the project link can be tested without a real
    audio file or a real transcription."""

    def project_get(self, name):
        if name != "demo":
            return None
        return (1, "demo", "/tmp/demo.wav", "en", "fr", "ff_siwis",
                1.0, 60.0, 0, 1700000000.0)

    def segment_list(self, project_id, limit=1000):
        if project_id != 1:
            return []
        return [
            (1, 1, 0, 1.0, 4.0, "Welcome to the lecture on tides.",
             None, 0, 1700000000.0),
            (2, 1, 1, 5.5, 9.0, "The moon pulls the oceans twice a day.",
             None, 0, 1700000000.0),
        ]


class TestMediaChat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_vidbee_")
        self._old_data = mc.DATA_DIR
        mc.DATA_DIR = self.tmp
        self._old_key = pt.config.api_key
        self._old_model = pt.config.model_name
        pt.config.api_key = FAKE_KEY
        pt.config.model_name = "fake-model"
        self._old_al_store = al._AlStore
        al._AlStore = _FakeAlStore
        self.opener = _FakeOpener("The moon pulls the oceans twice a day.")
        self.srt = os.path.join(self.tmp, "talk.srt")
        Path(self.srt).write_text(SRT_TEXT, encoding="utf-8")
        self.vtt = os.path.join(self.tmp, "talk.vtt")
        Path(self.vtt).write_text(VTT_TEXT, encoding="utf-8")
        self.txt = os.path.join(self.tmp, "talk.txt")
        Path(self.txt).write_text(PLAIN_TEXT, encoding="utf-8")

    def tearDown(self):
        mc.DATA_DIR = self._old_data
        pt.config.api_key = self._old_key
        pt.config.model_name = self._old_model
        al._AlStore = self._old_al_store

    def _add_file(self, name="talk"):
        out = mc.media_chat(action="add", media=name, mode="file",
                            file=self.srt)
        self.assertIn("added", out)
        return out

    # -- registry / census / catalogue / classification -----------------
    def test_registry_total_is_756(self):  # INTENTIONAL — R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        names = [d["name"] for d in _registry.get_declarations()]
        self.assertEqual(len(names), 756)  # INTENTIONAL — R19-4 VidBee pack (2026-11-12): 745 -> 746 — media_chat added; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(len(set(names)), 756)  # INTENTIONAL — R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine

    def test_registry_params_total_is_2078(self):  # INTENTIONAL — R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; GAPS-20 loopback-meeting pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine
        total = sum(len(d["parameters"].get("properties", {}))
                    for d in _registry.get_declarations())
        self.assertEqual(total, 2078)  # INTENTIONAL — R19-4 VidBee pack (2026-11-12): 1961 -> 1971 — media_chat has 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine

    def test_catalogue_grouped_is_753(self):  # INTENTIONAL — R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 753)  # INTENTIONAL — R19-4 VidBee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine

    def test_registry_closed_is_755(self):  # INTENTIONAL — R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine
        closed = sum(1 for d in _registry.get_declarations()
                     if d["parameters"].get("additionalProperties") is False)
        self.assertEqual(closed, 755)  # INTENTIONAL — R19-4 VidBee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine

    def test_media_chat_in_play_and_create(self):
        self.assertIn("media_chat", GROUP_MEMBERS["Play & create"])
        # The single catalogue source (core/catalogue_groups_data.py,
        # GAPS-23 F-V7) is checked as text: media_chat sits in the
        # Play & create block, after audio_localise.
        gui_text = open(TREE / "core" / "catalogue_groups_data.py",
                        encoding="utf-8").read()
        block = gui_text.index('"Play & create"')
        self.assertIn('"media_chat"', gui_text[block:])
        self.assertLess(gui_text.index('"audio_localise"'),
                        gui_text.index('"media_chat"'))

    def test_media_chat_has_10_properties(self):
        decl = [d for d in _registry.get_declarations()
                if d["name"] == "media_chat"]
        self.assertEqual(len(decl), 1)
        props = decl[0]["parameters"]["properties"]
        self.assertEqual(len(props), 10)
        self.assertEqual(sorted(props.keys()),
                         ["action", "detail", "file", "length", "limit",
                          "media", "mode", "name", "project", "question"])
        self.assertEqual(decl[0]["parameters"].get("required"), ["action"])

    def test_media_chat_confirm_gated(self):
        for action in ("ask", "summarize"):
            self.assertTrue(_needs_confirmation(
                "media_chat", {"action": action}), action)
        for action in ("add", "list", "get", "remove", "status",
                       "events"):
            self.assertFalse(_needs_confirmation(
                "media_chat", {"action": action}), action)
        self.assertTrue(_needs_confirmation("media_chat", {}))
        self.assertTrue(_needs_confirmation(
            "media_chat", {"action": "bogus"}))

    def test_media_chat_readclass(self):
        for action in ("list", "get", "status", "events"):
            self.assertTrue(_is_read_class(
                "media_chat", {"action": action}), action)
        for action in ("ask", "summarize", "add", "remove"):
            self.assertFalse(_is_read_class(
                "media_chat", {"action": action}), action)

    def test_classification_snapshot(self):
        snap = json.load(open(TREE / "tests" / "_classification_snapshot.json"))
        entry = snap["media_chat"]
        self.assertIs(entry["confirm"], True)
        self.assertIs(entry["free"], None)
        self.assertIs(entry["read"], False)

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        text = open(TREE / "core" / "media_chat.py",
                    encoding="utf-8").read()
        self.assertTrue("PROPOSED — parent rules" in text
                        or "Parent-ruled" in text)

    def test_no_network_or_foreign_imports(self):
        # The module docstring names the STOPs it refuses (yt-dlp,
        # torch, pyannote, FFmpeg), so the check is on the CODE, not
        # the prose: no banned import, no banned identifier, no
        # downloader function.
        import ast
        text = open(TREE / "core" / "media_chat.py",
                    encoding="utf-8").read()
        tree = ast.parse(text)
        imports = set()
        funcs = set()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.FunctionDef):
                funcs.add(node.name)
            elif isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        for banned in ("yt_dlp", "torch", "pyannote", "ffmpeg",
                       "subprocess", "urllib", "requests"):
            self.assertNotIn(banned, imports, banned)
            self.assertNotIn(banned, names, banned)
        self.assertFalse(any("download" in f for f in funcs))

    def test_reads_do_not_create_the_store_file(self):
        store_path = Path(self.tmp) / "media_chat.sqlite3"
        self.assertFalse(store_path.exists())
        out = mc.media_chat(action="status")
        self.assertIn("0 media item(s)", out)
        self.assertFalse(store_path.exists())
        out = mc.media_chat(action="list")
        self.assertIn("no media registered", out)
        self.assertFalse(store_path.exists())
        out = mc.media_chat(action="events", mode="verify")
        self.assertIn("intact (0 event(s))", out)
        self.assertFalse(store_path.exists())

    # -- transcript parsing ----------------------------------------------
    def test_srt_parsing(self):
        cues = mc._parse_cues(SRT_TEXT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["text"], "Welcome to the lecture on tides.")
        self.assertAlmostEqual(cues[0]["start"], 1.0)
        self.assertAlmostEqual(cues[1]["start"], 5.5)
        transcript = mc._cues_to_transcript(cues)
        self.assertIn("[00:01] Welcome to the lecture on tides.", transcript)
        self.assertIn("[00:05] The moon pulls the oceans twice a day.",
                      transcript)

    def test_vtt_parsing(self):
        cues = mc._parse_cues(VTT_TEXT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[1]["text"],
                         "The moon pulls the oceans twice a day.")

    def test_read_transcript_file(self):
        transcript, segments, err = mc._read_transcript_file(self.srt)
        self.assertIsNone(err)
        self.assertEqual(segments, 2)
        self.assertIn("tides", transcript)
        transcript, segments, err = mc._read_transcript_file(self.vtt)
        self.assertIsNone(err)
        self.assertEqual(segments, 2)
        transcript, segments, err = mc._read_transcript_file(self.txt)
        self.assertIsNone(err)
        self.assertIn("Tides follow the moon.", transcript)

    def test_read_transcript_file_bad_extension_refused(self):
        bad = os.path.join(self.tmp, "talk.mp3")
        Path(bad).write_bytes(b"\x00\x01\x02")
        transcript, segments, err = mc._read_transcript_file(bad)
        self.assertIsNone(transcript)
        self.assertIn("not a transcript file", err)

    # -- add ---------------------------------------------------------------
    def test_add_from_file_then_list_get(self):
        self._add_file()
        out = mc.media_chat(action="list")
        self.assertIn("'talk'", out)
        self.assertIn("2 segment(s)", out)
        out = mc.media_chat(action="get", media="talk")
        self.assertIn("Welcome to the lecture on tides.", out)
        self.assertIn("0 question(s)", out)

    def test_add_from_project(self):
        out = mc.media_chat(action="add", media="demo-talk",
                            mode="project", project="demo")
        self.assertIn("added", out)
        self.assertIn("audio_localise", out)
        out = mc.media_chat(action="get", media="demo-talk")
        self.assertIn("The moon pulls the oceans twice a day.", out)

    def test_add_from_missing_project_refused(self):
        out = mc.media_chat(action="add", media="nope",
                            mode="project", project="no-such-project")
        self.assertIn("refused", out)
        self.assertIn("no audio_localise project", out)

    def test_add_duplicate_refused(self):
        self._add_file()
        out = mc.media_chat(action="add", media="talk", mode="file",
                            file=self.srt)
        self.assertIn("refused", out)
        self.assertIn("already exists", out)

    def test_add_unknown_mode_refused(self):
        out = mc.media_chat(action="add", media="talk", mode="bogus",
                            file=self.srt)
        self.assertIn("refused", out)

    # -- ask / summarize ---------------------------------------------------
    def test_ask_answers_from_transcript(self):
        self._add_file()
        out = mc.media_chat(action="ask", media="talk",
                            question="What pulls the oceans?",
                            _opener=self.opener)
        self.assertIn("The moon pulls the oceans twice a day.", out)
        self.assertEqual(len(self.opener.calls), 1)
        # The prompt carries the transcript and the question.
        req = self.opener.calls[0]
        self.assertIn("tides", req.data.decode("utf-8"))
        self.assertIn("What pulls the oceans?",
                      req.data.decode("utf-8"))
        # The answer is stored with the transcript's hash.
        store = mc._McStore()
        row = store.media_get("talk")
        self.assertIsNotNone(row)
        qa = store.qa_list(row[0])
        self.assertEqual(len(qa), 1)
        self.assertEqual(qa[0][2], "What pulls the oceans?")
        self.assertEqual(qa[0][4], mc._transcript_hash(row[5]))

    def test_ask_empty_and_long_question_refused(self):
        self._add_file()
        out = mc.media_chat(action="ask", media="talk", question="  ",
                            _opener=self.opener)
        self.assertIn("refused", out)
        out = mc.media_chat(action="ask", media="talk",
                            question="x" * 1001, _opener=self.opener)
        self.assertIn("refused", out)
        self.assertIn("1,000-character", out)
        self.assertEqual(len(self.opener.calls), 0)

    def test_ask_without_key_refused(self):
        self._add_file()
        pt.config.api_key = ""
        out = mc.media_chat(action="ask", media="talk",
                            question="What pulls the oceans?",
                            _opener=self.opener)
        self.assertIn("refused", out)
        self.assertEqual(len(self.opener.calls), 0)

    def test_summarize_short_and_long(self):
        self._add_file()
        out = mc.media_chat(action="summarize", media="talk",
                            length="short", _opener=self.opener)
        self.assertIn("The moon pulls the oceans twice a day.", out)
        out = mc.media_chat(action="summarize", media="talk",
                            length="long", _opener=self.opener)
        self.assertIn("The moon pulls the oceans twice a day.", out)
        store = mc._McStore()
        row = store.media_get("talk")
        summaries = store.summary_list(row[0])
        self.assertEqual(len(summaries), 2)
        self.assertEqual({s[2] for s in summaries}, {"short", "long"})

    def test_summarize_bad_length_refused(self):
        self._add_file()
        out = mc.media_chat(action="summarize", media="talk",
                            length="huge", _opener=self.opener)
        self.assertIn("refused", out)
        self.assertEqual(len(self.opener.calls), 0)

    # -- remove / status / events ------------------------------------------
    def test_remove_then_get_refused(self):
        self._add_file()
        out = mc.media_chat(action="remove", media="talk")
        self.assertIn("removed", out)
        out = mc.media_chat(action="get", media="talk")
        self.assertIn("refused", out)
        self.assertIn("removed", out)

    def test_status_and_events(self):
        self._add_file()
        out = mc.media_chat(action="status")
        self.assertIn("1 media item(s)", out)
        self.assertIn("intact", out)
        self.assertIn("No downloader", out)
        out = mc.media_chat(action="events", mode="list")
        self.assertIn("media_add", out)
        self.assertIn("chain: intact", out)
        out = mc.media_chat(action="events", mode="verify")
        self.assertIn("intact (1 event(s))", out)

    def test_ledger_tamper_is_caught(self):
        self._add_file()
        store = mc._McStore()
        conn = sqlite3.connect(str(store.path))
        conn.execute("UPDATE events SET payload = '{\"kind\": \"x\"}' "
                     "WHERE id = 1")
        conn.commit()
        conn.close()
        ok, note, count = store.verify_chain()
        self.assertFalse(ok)
        self.assertIn("fails its hash", note)

    def test_unknown_action_refused(self):
        out = mc.media_chat(action="bogus")
        self.assertIn("refused", out)
        out = mc.media_chat()
        self.assertIn("refused", out)


if __name__ == "__main__":
    unittest.main()
