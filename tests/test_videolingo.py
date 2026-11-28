"""Suite for R19-1 (Huanshere/VideoLingo inspiration):
core/audio_localise.py.

Fake STT / fake Gemini endpoint / fake TTS only: the pipeline is
driven through injectable fakes against a temp DATA_DIR, with a fake
key (obviously fake). No real STT, no real LM, no real TTS, no real
key is ever used. The 'key' strings below are obviously fake.
"""
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import core.audio_localise as al
import core.prompt_tune as pt

FAKE_KEY = "test-videolingo-fake-key-12345"


def _make_wav(path, seconds=4.0, rate=16000, freq=440.0):
    """A small mono 16-bit WAV (a sine wave), pure stdlib."""
    n = int(seconds * rate)
    frames = b"".join(
        struct.pack("<h", int(0.5 * 32767.0
                              * math.sin(2.0 * math.pi * freq * i / rate)))
        for i in range(n))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(frames)
    return path


class _FakeSTT:
    def __init__(self, segments):
        self.segments = segments
        self.calls = []

    def __call__(self, audio_16k, language):
        self.calls.append((len(audio_16k), language))
        return [dict(s) for s in self.segments]


class _FakeOpener:
    def __init__(self, translations):
        self.translations = translations
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        body = json.dumps({
            "candidates": [{"content": {"parts": [
                {"text": "\n".join(self.translations)}]}}],
        }).encode("utf-8")

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self, n=-1):
                return body

        return _Resp()


class _FakeTTS:
    def __init__(self, rate=24000, seconds=0.8):
        self.rate = rate
        self.seconds = seconds
        self.calls = []

    def speak(self, text, language=None, voice=None, speed=1.0,
              auto_switch=True, timestamp=None):
        import numpy as _np
        self.calls.append((text, language, voice, speed))
        n = int(self.seconds * self.rate)
        t = _np.arange(n, dtype=_np.float64) / self.rate
        samples = (0.3 * _np.sin(2.0 * math.pi * 440.0 * t)
                   ).astype(_np.float32)
        return {"ok": True, "voice": voice, "language": language,
                "audio": (samples, self.rate),
                "verification": {}, "fallback": None}


SEGMENTS = [
    {"start": 0.0, "end": 1.5, "text": "Hello world"},
    {"start": 1.5, "end": 3.0, "text": "This is a test"},
    {"start": 3.0, "end": 4.0, "text": "Goodbye"},
]
TRANSLATIONS = ["Bonjour le monde", "Ceci est un test", "Au revoir"]


class TestAudioLocalise(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="test_videolingo_")
        self._old_data = al.DATA_DIR
        al.DATA_DIR = self.tmp
        self._old_key = pt.config.api_key
        self._old_model = pt.config.model_name
        pt.config.api_key = FAKE_KEY
        pt.config.model_name = "fake-model"
        self.wav = _make_wav(os.path.join(self.tmp, "test.wav"))
        self.stt = _FakeSTT(SEGMENTS)
        self.opener = _FakeOpener(TRANSLATIONS)
        self.tts = _FakeTTS()
        out = al.audio_localise(action="project", mode="add",
                                project="demo", file=self.wav,
                                source_lang="en", target_lang="fr",
                                voice="ff_siwis", speed=1.0)
        self.assertIn("added (#", out)

    def tearDown(self):
        al.DATA_DIR = self._old_data
        pt.config.api_key = self._old_key
        pt.config.model_name = self._old_model

    # -- census pins ---------------------------------------------------
    def test_registry_total_is_772(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.funnel.tools_registry import tools
        self.assertEqual(len(tools.get_declarations()), 772)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_registry_params_total_is_2126(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; GAPS-20 loopback-meeting pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.funnel.tools_registry import tools
        total = sum(len(d.get("parameters", {}).get("properties", {}))
                    for d in tools.get_declarations())
        self.assertEqual(total, 2126)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_catalogue_grouped_is_772(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.catalogue_snapshot import GROUP_MEMBERS
        grouped = sum(len(m) for m in GROUP_MEMBERS.values())
        self.assertEqual(grouped, 772)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170; UI Phase 2 (2026-11-25): grouped 769 -> 772 — click_anywhere + 2 plugin tools join the catalogue

    def test_registry_closed_is_771(self):  # INTENTIONAL — R19-2 ortools pack (2026-11-12): stale method-name label renamed; R19-3 aureuserp pack (2026-11-12): stale method-name label renamed; R19-4 vidbee pack (2026-11-12): stale method-name label renamed; R19-5 invoify pack (2026-11-12): stale method-name label renamed; R19-6 tolgee pack (2026-11-12): stale method-name label renamed; R19-7 spoolman pack (2026-11-12): stale method-name label renamed; R19-8 djangocrm pack (2026-11-12): stale method-name label renamed; R19-10 vroom pack (2026-11-12): stale method-name label renamed; GAPS-7 connectors pack (2026-11-13): stale method-name label renamed; SOCIAL Phase 1 (2026-11-13): stale method-name label renamed; GAPS-8 scheduled-agents pack (2026-11-13): stale method-name label renamed; Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        from core.funnel.tools_registry import tools
        closed = sum(1 for d in tools.get_declarations()
                     if d.get("parameters", {}).get("additionalProperties")
                     is False)
        self.assertEqual(closed, 771)  # INTENTIONAL — R19-2 ortools pack (2026-11-12): 742 -> 743 — optimise added (additionalProperties False by the registry default, so the new tool is closed); R19-3 aureuserp pack (2026-11-12): 743 -> 744 — erp added (additionalProperties False by the registry default, so the new tool is closed); R19-4 vidbee pack (2026-11-12): 744 -> 745 — media_chat added (additionalProperties False by the registry default, so the new tool is closed); R19-5 invoify pack (2026-11-12): 745 -> 746 — invoice added (additionalProperties False by the registry default, so the new tool is closed); R19-6 tolgee pack (2026-11-12): 746 -> 747 — translations added (additionalProperties False by the registry default, so the new tool is closed); R19-7 spoolman pack (2026-11-12): 747 -> 748 — filament added (additionalProperties False by the registry default, so the new tool is closed); R19-8 djangocrm pack (2026-11-12): 748 -> 749 — deals added (additionalProperties False by the registry default, so the new tool is closed); R19-10 vroom pack (2026-11-12): 749 -> 750 — vehicle_routes added (additionalProperties False by the registry default, so the new tool is closed); GAPS-7 connectors pack (2026-11-13): 750 -> 751 — sync_updates added (additionalProperties False by the registry default, so the new tool is closed); SOCIAL Phase 1 (2026-11-13): 751 -> 752 — social_send added (additionalProperties False by the registry default, so the new tool is closed); GAPS-8 scheduled-agents pack (2026-11-13): 752 -> 753 — scheduled_agents added (additionalProperties False by the registry default, so the new tool is closed); Click-anywhere (2026-11-14): 753 -> 754 — click_anywhere's schema is closed; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_audio_localise_in_play_and_create(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        self.assertIn("audio_localise",
                      GROUP_MEMBERS.get("Play & create", ()))

    def test_audio_localise_has_13_properties(self):
        from core.funnel.tools_registry import tools
        decl = [d for d in tools.get_declarations()
                if d["name"] == "audio_localise"]
        self.assertEqual(len(decl), 1)
        self.assertEqual(len(decl[0]["parameters"]["properties"]), 13)
        self.assertEqual(decl[0]["parameters"].get("required"), ["action"])

    def test_licence_marker_is_proposed_or_parent_ruled(self):
        # The parent's ruling edit flips the marker after this suite
        # runs; the test accepts either state (the R18-1/R18-5 trap).
        src = Path(al.__file__).read_text(encoding="utf-8")
        self.assertTrue("PROPOSED — parent rules" in src
                        or "Parent-ruled" in src)
        self.assertIn("Huanshere/VideoLingo", src)
        self.assertIn("Apache-2.0", src)

    def test_classification_is_confirm_gated(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        snap = json.loads(Path(root, "tests",
                               "_classification_snapshot.json")
                          .read_text(encoding="utf-8"))
        self.assertEqual(snap.get("audio_localise"),
                         {"confirm": True, "free": None, "read": False})

    def test_translate_and_localise_gate(self):
        from core.funnel.tools_gate import _needs_confirmation as _nc
        self.assertTrue(_nc("audio_localise", {"action": "translate"}))
        self.assertTrue(_nc("audio_localise", {"action": "localise"}))
        self.assertFalse(_nc("audio_localise", {"action": "transcribe"}))
        self.assertFalse(_nc("audio_localise", {"action": "dub"}))
        self.assertFalse(_nc("audio_localise", {"action": "status"}))
        self.assertFalse(_nc("audio_localise",
                             {"action": "project", "mode": "add"}))

    def test_status_and_events_are_read_class(self):
        from core.funnel.tools_gate_readclass import _is_read_action as _ra
        self.assertTrue(_ra("audio_localise", {"action": "status"}))
        self.assertTrue(_ra("audio_localise", {"action": "events"}))
        self.assertFalse(_ra("audio_localise", {"action": "translate"}))
        self.assertFalse(_ra("audio_localise", {"action": "dub"}))

    # -- the pipeline --------------------------------------------------
    def test_reads_do_not_create_the_store(self):
        # setUp created the store (the project add); remove it, then
        # a pure read must not recreate it.
        db = os.path.join(self.tmp, "audio_localise.sqlite3")
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                os.remove(db + suffix)
            except FileNotFoundError:
                pass
        self.assertFalse(os.path.exists(db))
        out = al.audio_localise(action="status", project="nope")
        self.assertIn("no project named", out)
        self.assertFalse(os.path.exists(db))

    def test_transcribe_returns_segments(self):
        out = al.audio_localise(action="transcribe", project="demo",
                                _stt=self.stt)
        self.assertIn("3 segments", out)
        self.assertEqual(len(self.stt.calls), 1)

    def test_translate_writes_subtitle_files(self):
        al.audio_localise(action="transcribe", project="demo",
                          _stt=self.stt)
        out = al.audio_localise(action="translate", project="demo",
                                _opener=self.opener)
        self.assertIn("Translated 3 segment(s)", out)
        out_dir = Path(self.tmp) / "audio_localise" / "demo"
        srt = out_dir / "subtitles.fr.srt"
        vtt = out_dir / "subtitles.fr.vtt"
        self.assertTrue(srt.is_file())
        self.assertTrue(vtt.is_file())
        self.assertIn("Bonjour le monde",
                      srt.read_text(encoding="utf-8"))
        self.assertTrue(vtt.read_text(encoding="utf-8")
                        .startswith("WEBVTT"))

    def test_translate_refuses_a_line_count_mismatch(self):
        al.audio_localise(action="transcribe", project="demo",
                          _stt=self.stt)
        bad = _FakeOpener(["only one line"])
        out = al.audio_localise(action="translate", project="demo",
                                _opener=bad)
        self.assertIn("the alignment cannot be trusted", out)

    def test_translate_refuses_without_a_key(self):
        al.audio_localise(action="transcribe", project="demo",
                          _stt=self.stt)
        pt.config.api_key = ""
        try:
            out = al.audio_localise(action="translate", project="demo",
                                    _opener=self.opener)
        finally:
            pt.config.api_key = FAKE_KEY
        self.assertIn("no Gemini key is configured", out)

    def test_dub_writes_a_valid_wav(self):
        al.audio_localise(action="transcribe", project="demo",
                          _stt=self.stt)
        al.audio_localise(action="translate", project="demo",
                          _opener=self.opener)
        out = al.audio_localise(action="dub", project="demo", _tts=self.tts)
        self.assertIn("dub.fr.wav", out)
        self.assertEqual(len(self.tts.calls), 3)
        dub_path = Path(self.tmp) / "audio_localise" / "demo" / "dub.fr.wav"
        self.assertTrue(dub_path.is_file())
        with wave.open(str(dub_path), "rb") as wf:
            self.assertEqual(wf.getframerate(), 24000)
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getsampwidth(), 2)
            self.assertAlmostEqual(wf.getnframes() / 24000.0, 4.0,
                                   delta=0.5)

    def test_localise_runs_the_full_pipeline(self):
        out = al.audio_localise(action="localise", project="demo",
                                _stt=self.stt, _opener=self.opener,
                                _tts=self.tts)
        self.assertIn("Localised 'demo'", out)
        self.assertIn("3 segments", out)
        self.assertIn("subtitles.fr.srt", out)
        self.assertIn("dub.fr.wav", out)

    def test_status_shows_counts(self):
        al.audio_localise(action="transcribe", project="demo",
                          _stt=self.stt)
        al.audio_localise(action="translate", project="demo",
                          _opener=self.opener)
        al.audio_localise(action="dub", project="demo", _tts=self.tts)
        out = al.audio_localise(action="status", project="demo")
        self.assertIn("3 transcribed, 3 translated, 3 dubbed", out)

    def test_ledger_verifies_and_tamper_is_caught(self):
        al.audio_localise(action="transcribe", project="demo",
                          _stt=self.stt)
        out = al.audio_localise(action="events", mode="verify")
        self.assertIn("chain is intact", out)
        import sqlite3
        db = Path(self.tmp) / "audio_localise.sqlite3"
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE events SET payload = '{}' WHERE id = 1")
        conn.commit()
        conn.close()
        out = al.audio_localise(action="events", mode="verify")
        self.assertIn("BROKEN", out)

    def test_project_remove_and_restore(self):
        al.audio_localise(action="transcribe", project="demo",
                          _stt=self.stt)
        out = al.audio_localise(action="project", mode="remove",
                                project="demo")
        self.assertIn("removed (soft-delete)", out)
        out = al.audio_localise(action="project", mode="add",
                                project="demo", file=self.wav,
                                source_lang="en", target_lang="fr",
                                voice="ff_siwis")
        self.assertIn("restored with its segments", out)

    # -- refusals ------------------------------------------------------
    def test_project_refuses_a_non_wav_file(self):
        mp3 = os.path.join(self.tmp, "x.mp3")
        Path(mp3).write_bytes(b"not audio")
        out = al.audio_localise(action="project", mode="add",
                                project="bad", file=mp3,
                                source_lang="en", target_lang="fr",
                                voice="ff_siwis")
        self.assertIn("not a WAV file", out)

    def test_project_refuses_source_equals_target(self):
        out = al.audio_localise(action="project", mode="add",
                                project="bad", file=self.wav,
                                source_lang="en", target_lang="en")
        self.assertIn("both 'en'", out)

    def test_project_refuses_a_voice_that_does_not_match_the_target(self):
        out = al.audio_localise(action="project", mode="add",
                                project="bad", file=self.wav,
                                source_lang="en", target_lang="fr",
                                voice="af_heart")
        self.assertIn("must match the target", out)

    def test_project_refuses_an_undubbable_target_language(self):
        out = al.audio_localise(action="project", mode="add",
                                project="bad", file=self.wav,
                                source_lang="en", target_lang="de")
        self.assertIn("no voice for 'de'", out)

    def test_project_refuses_a_speed_over_4(self):
        out = al.audio_localise(action="project", mode="add",
                                project="bad", file=self.wav,
                                source_lang="en", target_lang="fr",
                                voice="ff_siwis", speed=5.0)
        self.assertIn("speed must be in", out)

    def test_transcribe_refuses_without_faster_whisper(self):
        out = al.audio_localise(action="transcribe", project="demo")
        self.assertIn("faster-whisper is not installed", out)

    def test_dub_refuses_without_translated_segments(self):
        out = al.audio_localise(action="dub", project="demo", _tts=self.tts)
        self.assertIn("no translated segments", out)

    def test_unknown_action_is_refused(self):
        out = al.audio_localise(action="explode", project="demo")
        self.assertIn("must be one of", out)


if __name__ == "__main__":
    unittest.main()
