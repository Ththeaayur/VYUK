"""R7-4 — meetily pattern: live meeting capture -> transcribe -> notes (tests/test_meetily.py).

The loophole family: "the meeting lies, or the meeting is missing" —
a transcript shown as real when it was fabricated; notes shown for
audio that was never captured; a meeting recorded without the user
asking, without a visible indicator, or sent somewhere; speaker
labels claimed with no diarisation. ONE design: one meeting session
— capture starts only on an explicit start_meeting call gated by the
capture guard; the indicator brackets the whole session; the
transcript comes only from asr.py (faster-whisper) over the recorded
WAV; the notes come only from produce_meeting_notes over that
transcript; nothing leaves the machine (no network import in the
module); no speaker labels are ever claimed.

Fakes only: a fake capture backend feeding synthetic PCM, a fake
transcriber, a stubbed capture guard. No real mic, no real audio, no
faster-whisper (not installed in the lab). The real-Windows leg (a
real meeting on his machine, with his mic and his meeting audio) is
a field leg, owed, not this suite.
"""

import json
import math
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from core import capture_guard
from core import meeting as meeting_mod
from core.funnel.tools_registry import tools  # GAPS-23 (F-V7): meeting tests run through the funnel
from core.daily_meeting import MeetingGate, produce_meeting_notes
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tools_registry import tools as _registry

_MEETING_TOOLS = ("start_meeting", "stop_meeting", "meeting_status", "transcribe_audio")


def _sine_pcm(seconds: float = 0.5, freq: float = 440.0, rate: int = 16000) -> bytes:
    """Synthetic 16 kHz mono int16 PCM (a sine burst — test audio only)."""
    n = int(seconds * rate)
    samples = [
        int(12000 * math.sin(2.0 * math.pi * freq * i / rate)) for i in range(n)
    ]
    return struct.pack(f"<{n}h", *samples)


class _ManualBackend:
    """Fake capture backend: the test feeds PCM by hand, then closes.

    Mirrors the real backend's protocol (open(on_chunk) / close() /
    .overflows) without touching any audio device.
    """

    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.overflows = 0
        self._on_chunk = None

    def open(self, on_chunk) -> None:
        self.opened = True
        self._on_chunk = on_chunk

    def feed(self, pcm_bytes: bytes) -> None:
        if self._on_chunk is not None:
            self._on_chunk(pcm_bytes)

    def close(self) -> None:
        self.closed = True


def _write_wav(path: Path, pcm_bytes: bytes, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm_bytes)


class TestMeetilyRegistration(unittest.TestCase):
    def test_four_tools_registered(self):
        names = [d["name"] for d in _registry.get_declarations()]
        for name in _MEETING_TOOLS:
            self.assertIn(name, names, f"{name} is not registered")

    def test_registry_total_is_758(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(len(_registry.get_declarations()), 758)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_classification_values(self):
        # start/stop mirror record_audio (capture, never confirm-gated);
        # status/transcribe mirror read_pdf_text (pure reads).
        self.assertEqual(_FREE_BY_DESIGN.get("start_meeting"), "capture")
        self.assertEqual(_FREE_BY_DESIGN.get("stop_meeting"), "capture")
        self.assertIn("meeting_status", _READ_ONLY_TOOLS)
        self.assertIn("transcribe_audio", _READ_ONLY_TOOLS)
        for name in _MEETING_TOOLS:
            self.assertNotIn(name, _CONFIRM_GATE_TOOLS, name)

    def test_deadline_classes(self):
        from core.funnel.tools_handlers_limits import _TOOL_DEADLINE_CLASS

        # Transcription scales with the recording's length — both take long.
        self.assertEqual(_TOOL_DEADLINE_CLASS.get("stop_meeting"), "long")
        self.assertEqual(_TOOL_DEADLINE_CLASS.get("transcribe_audio"), "long")
        # start/status return at once; the default (standard) applies.
        self.assertNotIn("start_meeting", _TOOL_DEADLINE_CLASS)
        self.assertNotIn("meeting_status", _TOOL_DEADLINE_CLASS)

    def test_catalogue_grouped_in_files_and_media(self):
        # Read GROUP_MEMBERS from the single catalogue source
        # (core/catalogue_groups_data.py, GAPS-23 F-V7) as text,
        # the same shape test_wave8_feel.py parses it.
        import ast

        source = Path(__file__).resolve().parent.parent.joinpath(
            "core", "catalogue_groups_data.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        groups = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, ast.AnnAssign):
                targets, value = [node.target], node.value
            else:
                continue
            for target in targets:
                if (isinstance(target, ast.Name)
                        and target.id == "GROUP_MEMBERS" and value is not None):
                    groups = ast.literal_eval(value)
        grouped = groups.get("Files & media", ())
        for name in _MEETING_TOOLS:
            self.assertIn(name, grouped, f"{name} is not grouped")

    def test_no_network_imports(self):
        import ast

        src = Path(meeting_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned = {"socket", "requests", "urllib", "http", "httpx", "aiohttp"}
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        self.assertEqual(imported & banned, set(), "meeting.py imports a network module")

    def test_no_diarisation_anywhere(self):
        import ast

        src = Path(meeting_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        # The docstring NAMES pyannote to explain why it is absent;
        # what matters is that no code imports or calls it.
        self.assertNotIn("pyannote", imported)
        self.assertNotIn("import pyannote", src)
        self.assertNotIn("speaker_id", src)
        self.assertIn("Speaker labels are not available", src)


class TestMeetilySession(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        # All meeting files go to the tmp dir, never the real data/.
        self._data_patch = mock.patch.object(meeting_mod, "_DATA_DIR", self._tmp_path)
        self._data_patch.start()
        # The lab mic is off in Settings; stub the guard to allow.
        self._guard_patch = mock.patch.object(
            capture_guard, "ensure_capture_allowed",
            return_value=(True, "ok"),
        )
        self._guard_patch.start()
        self._started_patch = mock.patch.object(
            capture_guard, "note_capture_started")
        self._started = self._started_patch.start()
        self._ended_patch = mock.patch.object(
            capture_guard, "note_capture_ended")
        self._ended = self._ended_patch.start()
        self._session = meeting_mod.get_session()  # the live singleton: the funnel handlers use it too
        self._backend = _ManualBackend()
        meeting_mod.set_capture_backend(self._backend)
        # Fake transcriber: canned text, records the PCM it was given.
        self._seen_pcm: list[bytes] = []

        def _fake_transcribe(pcm_bytes):
            self._seen_pcm.append(bytes(pcm_bytes))
            return {
                "engine": "faster-whisper",
                "text": "discussed the launch plan\nAction: Aayur will send the timeline",
                "segments": [],
                "language": "en",
                "model_arg": "small",
                "elapsed_s": 0.01,
                "error": None,
            }

        self._transcribe_patch = mock.patch.object(
            meeting_mod, "_transcribe_pcm", side_effect=_fake_transcribe)
        self._transcribe_patch.start()

    def tearDown(self):
        # Never leak a recording into the next test: stop the
        # singleton first, while the fakes are still patched.
        if self._session.state == "recording":
            try:
                self._session.stop()
            except Exception:  # noqa: BLE001 — tearDown is best-effort
                pass
        self._transcribe_patch.stop()
        meeting_mod.reset_seams()
        self._ended_patch.stop()
        self._started_patch.stop()
        self._guard_patch.stop()
        self._data_patch.stop()
        self._tmp.cleanup()

    def test_status_idle_before_start(self):
        self.assertEqual(self._session.status()["state"], "idle")
        self.assertEqual(self._session.status()["wav_path"], None)

    def test_stop_without_start_refuses(self):
        ok, result = self._session.stop()
        self.assertFalse(ok)
        self.assertIn("No meeting is recording", result["error"])

    def test_start_refuses_when_guard_denies(self):
        with mock.patch.object(
            capture_guard, "ensure_capture_allowed",
            return_value=(False, "Microphone is switched off in Settings"),
        ):
            ok, message = self._session.start("standup")
        self.assertFalse(ok)
        self.assertIn("switched off", message)
        self.assertFalse(self._backend.opened)
        self.assertEqual(self._session.status()["state"], "idle")

    def test_start_opens_stream_and_indicator(self):
        ok, message = self._session.start("standup")
        self.assertTrue(ok, message)
        self.assertTrue(self._backend.opened)
        self.assertEqual(self._session.status()["state"], "recording")
        self.assertEqual(self._session.status()["title"], "standup")
        self._started.assert_called_once_with("microphone")
        self._ended.assert_not_called()

    def test_second_start_refuses(self):
        self._session.start("one")
        ok, message = self._session.start("two")
        self.assertFalse(ok)
        self.assertIn("already recording", message)

    def test_full_pipeline_end_to_end(self):
        ok, _ = self._session.start("launch review")
        self.assertTrue(ok)
        pcm = _sine_pcm(0.5)
        self._backend.feed(pcm)
        self._backend.feed(pcm)
        ok, result = self._session.stop()
        self.assertTrue(ok, result)
        # The transcriber got the REAL recorded audio, byte for byte.
        self.assertEqual(len(self._seen_pcm), 1)
        self.assertEqual(self._seen_pcm[0], pcm + pcm)
        # The transcript fed produce_meeting_notes end to end.
        self.assertIn("discussed the launch plan", result["transcript"])
        notes = result["notes"]
        self.assertIsNotNone(notes)
        self.assertIn("discussed the launch plan", notes["summary"])
        self.assertTrue(
            any("Aayur will send the timeline" in item
                for item in notes["action_items"]),
            notes["action_items"],
        )
        # Files land in data/recordings/ with the date in the name.
        wav_path = Path(result["wav_path"])
        self.assertEqual(wav_path.parent, self._tmp_path / "recordings")
        self.assertRegex(wav_path.name, r"^meeting_\d{8}_\d{6}\.wav$")
        self.assertTrue(wav_path.is_file())
        self.assertGreater(wav_path.stat().st_size, 0)
        # A4-filestat: the sizes ride the result (re-read after write).
        self.assertEqual(result["wav_bytes"], wav_path.stat().st_size)
        transcript_path = Path(result["transcript_path"])
        notes_path = Path(result["notes_path"])
        self.assertTrue(transcript_path.is_file())
        self.assertTrue(notes_path.is_file())
        self.assertEqual(
            json.loads(notes_path.read_text(encoding="utf-8")), notes)
        # No speaker labels are ever claimed.
        self.assertIn("Speaker labels are not available",
                      transcript_path.read_text(encoding="utf-8"))
        # The indicator ends only after the notes are written.
        self._ended.assert_called_once_with("microphone")
        self.assertEqual(self._session.status()["state"], "idle")
        self.assertTrue(self._backend.closed)

    def test_stop_reports_bytes_in_prose(self):
        self._session.start()
        self._backend.feed(_sine_pcm(0.25))
        prose = tools.execute("stop_meeting", {})["result"]
        self.assertIn("bytes", prose)
        self.assertIn("Speaker labels are not available", prose)

    def test_engine_unavailable_is_honest(self):
        def _unavailable(pcm_bytes):
            return {"engine": "unavailable", "text": "", "segments": [],
                    "language": None, "model_arg": "small",
                    "elapsed_s": 0.0, "error": "faster-whisper is not installed"}

        with mock.patch.object(meeting_mod, "_transcribe_pcm",
                               side_effect=_unavailable):
            self._session.start()
            self._backend.feed(_sine_pcm(0.25))
            ok, result = self._session.stop()
        self.assertTrue(ok)
        self.assertIsNone(result["notes"])
        self.assertIn("not installed", result["error"])
        transcript = Path(result["transcript_path"]).read_text(encoding="utf-8")
        self.assertIn("unavailable", transcript)
        # Nothing fabricated: no transcript text, no notes file.
        self.assertEqual(result["transcript"], "")
        self.assertFalse(Path(result["notes_path"]).exists())

    def test_no_audio_is_honest(self):
        self._session.start()
        ok, result = self._session.stop()
        self.assertTrue(ok)
        self.assertEqual(result["error"], "No audio was captured in the meeting.")
        self.assertIsNone(result["notes"])

    def test_same_second_meetings_do_not_overwrite(self):
        # A fixed stem forces the collision: the second meeting must
        # land on meeting_..._2.wav, never truncate the first.
        with mock.patch.object(meeting_mod, "_meeting_stem",
                               return_value="meeting_20261110_120000"):
            ok, _ = self._session.start("first")
            self.assertTrue(ok)
            first_pcm = _sine_pcm(0.25, freq=440.0)
            self._backend.feed(first_pcm)
            ok, first = self._session.stop()
            self.assertTrue(ok)
            ok, _ = self._session.start("second")
            self.assertTrue(ok)
            self._backend.feed(_sine_pcm(0.25, freq=880.0))
            ok, second = self._session.stop()
            self.assertTrue(ok)
        first_path = Path(first["wav_path"])
        second_path = Path(second["wav_path"])
        self.assertNotEqual(first_path, second_path)
        self.assertEqual(second_path.name, "meeting_20261110_120000_2.wav")
        self.assertTrue(first_path.is_file())
        # The first recording's bytes survived untouched.
        with wave.open(str(first_path), "rb") as wav:
            self.assertEqual(wav.readframes(wav.getnframes()), first_pcm)

    def test_meeting_status_prose(self):
        self.assertIn("No meeting is recording",
                      tools.execute("meeting_status", {})["result"])
        self._session.start("sync")
        prose = tools.execute("meeting_status", {})["result"]
        self.assertIn("Recording a meeting", prose)
        self.assertIn("sync", prose)
        self.assertIn("recording indicator is on", prose)


class TestMeetilyTranscribeAudio(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)
        self._data_patch = mock.patch.object(meeting_mod, "_DATA_DIR", self._tmp_path)
        self._data_patch.start()
        self._seen: list[bytes] = []

        def _fake(pcm_bytes):
            self._seen.append(bytes(pcm_bytes))
            return {"engine": "faster-whisper", "text": "hello meeting",
                    "segments": [], "language": "en", "model_arg": "small",
                    "elapsed_s": 0.01, "error": None}

        self._transcribe_patch = mock.patch.object(
            meeting_mod, "_transcribe_pcm", side_effect=_fake)
        self._transcribe_patch.start()

    def tearDown(self):
        self._transcribe_patch.stop()
        self._data_patch.stop()
        self._tmp.cleanup()

    def _recording(self, name, pcm, rate=16000):
        rec_dir = meeting_mod._meeting_dir()
        path = rec_dir / name
        _write_wav(path, pcm, rate)
        return path

    def test_transcribe_audio_calls_asr(self):
        self._recording("meeting_20261110_120000.wav", _sine_pcm(0.5))
        out = tools.execute("transcribe_audio", {"filename": "meeting_20261110_120000.wav"})["result"]
        self.assertIn("hello meeting", out)
        self.assertIn("no speaker labels", out)
        self.assertEqual(len(self._seen), 1)

    def test_transcribe_audio_resamples_44k(self):
        # The tree's own record_audio clips are 44.1 kHz — the tool
        # converts them explicitly instead of refusing them.
        self._recording("clip_44100.wav", _sine_pcm(0.5, rate=44100), rate=44100)
        out = tools.execute("transcribe_audio", {"filename": "clip_44100.wav"})["result"]
        self.assertIn("hello meeting", out)
        got = self._seen[0]
        # 0.5 s at 44.1 kHz -> 0.5 s at 16 kHz (8000 samples).
        self.assertEqual(len(got), 16000)

    def test_resampler_length_ratio(self):
        pcm = _sine_pcm(1.0, rate=44100)
        out = meeting_mod._resample_to_16k(pcm, 44100)
        self.assertEqual(len(out), 32000)  # 1 s * 16000 Hz * 2 bytes
        self.assertEqual(meeting_mod._resample_to_16k(pcm, 16000), pcm)

    def test_transcribe_audio_refuses_paths(self):
        out = tools.execute("transcribe_audio", {"filename": "../secret.wav"})["result"]
        self.assertIn("just its file name", out)
        out = tools.execute("transcribe_audio", {"filename": "missing.wav"})["result"]
        self.assertIn("No recording named missing.wav", out)
        out = tools.execute("transcribe_audio", {"filename": "notes.txt"})["result"]
        self.assertIn("not a .wav", out)

    def test_transcribe_audio_engine_unavailable(self):
        self._recording("meeting_20261110_120001.wav", _sine_pcm(0.25))

        def _unavailable(pcm_bytes):
            return {"engine": "unavailable", "text": "", "segments": [],
                    "language": None, "model_arg": "small",
                    "elapsed_s": 0.0, "error": "faster-whisper is not installed"}

        with mock.patch.object(meeting_mod, "_transcribe_pcm",
                               side_effect=_unavailable):
            out = tools.execute(
                "transcribe_audio", {"filename": "meeting_20261110_120001.wav"})["result"]
        self.assertIn("unavailable", out)
        self.assertIn("still saved", out)


class TestMeetilyGapPins(unittest.TestCase):
    def test_produce_meeting_notes_is_transcript_in(self):
        # The gap, pinned: the summariser takes a TRANSCRIPT string.
        gate = MeetingGate(opt_in=True, indicator_on=True)
        notes, error = produce_meeting_notes("hello world\nAction: ship it", gate)
        self.assertIsNone(error)
        self.assertEqual(notes["summary"], "hello world Action: ship it")

    def test_gate_refuses_without_opt_in_or_indicator(self):
        notes, error = produce_meeting_notes(
            "hello", MeetingGate(opt_in=False, indicator_on=True))
        self.assertIsNone(notes)
        self.assertIn("not opted in", error)
        notes, error = produce_meeting_notes(
            "hello", MeetingGate(opt_in=True, indicator_on=False))
        self.assertIsNone(notes)
        self.assertIn("indicator is not on", error)


if __name__ == "__main__":
    unittest.main()
