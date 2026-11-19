"""R8-7 basic-pitch — audio-to-MIDI transcription as VYUK's own code
(PORT, not adopt: the basic-pitch package hard-requires TensorFlow
on Linux Python >= 3.11 plus librosa / scipy / scikit-learn /
mir-eval / resampy; what is ported is the ICASSP-2022 model run over
the tree's own onnxruntime (Qt only) with own numpy post-processing
— no TensorFlow, no librosa, no basic-pitch package. The model is
operator-supplied only (data/models/basic-pitch/nmp.onnx, pinned
sha256), never bundled. Pins: WAV reading (8/16/24/32-bit PCM,
stereo mixdown, honest refusals), the Kaiser-windowed-sinc
resampler (identity, fidelity, anti-aliasing), the post-processing
on synthetic posteriorgrams (including the measured +1 contour-bin
correction), pitch-bend MIDI write/read round-trip, the funnel
registration + gates + catalogue, and the honest no-model /
no-runtime refusals. Real-model tests run only where the lab model
file is present (~/workspace/code-lab/basic_pitch_models/nmp.onnx)
with onnxruntime available — they skip honestly elsewhere (system
python3 in this lab has no onnxruntime).
"""

from __future__ import annotations

import math
import os
import pathlib
import struct
import tempfile
import unittest
import unittest.mock
import wave

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import basic_pitch
from core.basic_pitch import (
    audio_to_midi,
    audio_to_midi_status,
    get_pitch_bends,
    midi_pitch_to_contour_bin,
    model_output_to_notes,
    model_status,
    read_wav,
    resample,
)
from core.creative_midi_core import read_midi, write_midi
from core.funnel.tools_gate import (
    _CONFIRM_GATE_TOOLS,
    _FILE_READ_PATH_ARGS,
    _FOLDER_SCOPE_PATH_ARGS,
    _FREE_BY_DESIGN,
    _WRITE_DESTINATION_ARGS,
)
from core.funnel.tools_registry import tools as _registry

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAB_MODEL = pathlib.Path.home() / "workspace" / "code-lab" / "basic_pitch_models" / "nmp.onnx"


def _onnxruntime_available() -> bool:
    try:
        import onnxruntime  # noqa: F401 — availability probe only
    except ImportError:
        return False
    return True


def _real_model_available() -> bool:
    return _onnxruntime_available() and LAB_MODEL.exists()


def _write_wav(path, samples, sr, n_channels=1, sample_width=2):
    """Write float32 samples in [-1, 1] as a PCM WAV."""
    frames = np.asarray(samples, dtype=np.float64)
    if n_channels == 2:
        frames = np.column_stack([frames, frames])
    n = len(frames)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(n_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sr)
        if sample_width == 1:
            data = np.clip(frames * 128 + 128, 0, 255).astype(np.uint8)
            wf.writeframes(data.reshape(n, n_channels).tobytes())
        elif sample_width == 2:
            data = np.clip(frames * 32768, -32768, 32767).astype("<i2")
            wf.writeframes(data.reshape(n, n_channels).tobytes())
        elif sample_width == 3:
            values = np.clip(frames * 8388608, -8388608, 8388607).astype(np.int32)
            flat = values.reshape(-1)
            packed = np.empty((len(flat), 3), dtype=np.uint8)
            packed[:, 0] = flat & 0xFF
            packed[:, 1] = (flat >> 8) & 0xFF
            packed[:, 2] = (flat >> 16) & 0xFF
            wf.writeframes(packed.tobytes())
        elif sample_width == 4:
            data = np.clip(frames * 2147483648, -2147483648,
                           2147483647).astype("<i4")
            wf.writeframes(data.reshape(n, n_channels).tobytes())
        else:
            raise ValueError("bad sample width")


def _sine(freq, seconds, sr, amplitude=0.9):
    t = np.arange(int(seconds * sr), dtype=np.float64) / sr
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _middle_rms(x):
    third = len(x) // 3
    return float(np.sqrt(np.mean(np.asarray(x[third:2 * third]) ** 2)))


class TestWavReading(unittest.TestCase):
    def test_16bit_mono_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tone.wav")
            _write_wav(path, _sine(440, 0.5, 22050), 22050)
            samples, sr = read_wav(path)
            self.assertEqual(sr, 22050)
            self.assertEqual(samples.dtype, np.float32)
            self.assertAlmostEqual(float(np.max(np.abs(samples))), 0.9, delta=0.001)

    def test_8_24_32_bit_round_trip(self):
        for width, tol in ((1, 0.01), (3, 0.000001), (4, 0.000001)):
            with self.subTest(width=width), tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "tone.wav")
                _write_wav(path, _sine(440, 0.2, 22050), 22050,
                           sample_width=width)
                samples, sr = read_wav(path)
                self.assertEqual(sr, 22050)
                self.assertAlmostEqual(float(np.max(np.abs(samples))), 0.9,
                                       delta=tol)

    def test_stereo_mixed_to_mono(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "stereo.wav")
            _write_wav(path, _sine(440, 0.2, 22050), 22050, n_channels=2)
            samples, sr = read_wav(path)
            self.assertEqual(len(samples), int(0.2 * 22050))
            self.assertAlmostEqual(float(np.max(np.abs(samples))), 0.9,
                                   delta=0.001)

    def test_non_wav_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "not.wav")
            pathlib.Path(path).write_text("this is not a wav file")
            with self.assertRaises(ValueError):
                read_wav(path)

    def test_empty_wav_refused_by_transcribe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.wav")
            _write_wav(path, np.zeros(0, dtype=np.float32), 22050)
            with self.assertRaises(ValueError):
                basic_pitch.transcribe(path)


class TestResampler(unittest.TestCase):
    def test_identity_at_22050(self):
        x = _sine(440, 0.5, 22050)
        np.testing.assert_array_equal(resample(x, 22050), x)

    def test_44100_to_22050_keeps_tone(self):
        x = _sine(440, 1.0, 44100)
        y = resample(x, 44100, 22050)
        self.assertEqual(len(y), 22050)
        self.assertAlmostEqual(_middle_rms(y) * math.sqrt(2), 0.9, delta=0.02)
        spec = np.abs(np.fft.rfft(y[len(y) // 3:2 * len(y) // 3]))
        freqs = np.fft.rfftfreq(len(y) // 3, 1 / 22050)
        self.assertAlmostEqual(float(freqs[int(np.argmax(spec))]), 440, delta=5)

    def test_48000_to_22050_keeps_tone(self):
        x = _sine(440, 1.0, 48000)
        y = resample(x, 48000, 22050)
        self.assertEqual(len(y), 22050)
        self.assertAlmostEqual(_middle_rms(y) * math.sqrt(2), 0.9, delta=0.02)

    def test_upsample_16000_to_22050_keeps_tone(self):
        x = _sine(440, 1.0, 16000)
        y = resample(x, 16000, 22050)
        self.assertEqual(len(y), 22050)
        self.assertAlmostEqual(_middle_rms(y) * math.sqrt(2), 0.9, delta=0.02)

    def test_antialias_kills_above_nyquist(self):
        # 15 kHz at 44100 Hz would alias to 7050 Hz at 22050 Hz if it
        # were not filtered — the sinc low-pass must kill it (10 kHz
        # would NOT alias: it is below the 11025 Hz output Nyquist).
        x = _sine(15000, 1.0, 44100)
        y = resample(x, 44100, 22050)
        self.assertLess(_middle_rms(y), 0.05 * _middle_rms(x))


class TestPostProcessing(unittest.TestCase):
    def _synthetic(self, note_frames=(10, 40), freq_idx=48,
                   contour_bin=145):
        frames = np.zeros((200, 88))
        onsets = np.zeros((200, 88))
        contours = np.zeros((200, 264))
        frames[note_frames[0]:note_frames[1], freq_idx] = 0.9
        onsets[note_frames[0], freq_idx] = 0.9
        contours[note_frames[0]:note_frames[1], contour_bin] = 0.8
        return {"note": frames, "onset": onsets, "contour": contours}

    def test_note_extracted_from_synthetic_posteriorgrams(self):
        notes = model_output_to_notes(self._synthetic())
        self.assertEqual(len(notes), 1)
        start, end, pitch, amp, _bends = notes[0]
        self.assertEqual(pitch, 69)
        self.assertAlmostEqual(amp, 0.9, delta=0.01)
        self.assertLess(start, 0.5)
        self.assertGreater(end, start)

    def test_contour_bin_is_one_above_upstream_formula(self):
        # Upstream: 36*log2(f/27.5) = 144.0 for A4. The model's contour
        # peak sits at 145 — the port's +1 correction, measured on
        # five pure tones through the real model.
        upstream = 12.0 * 3 * math.log2(440.0 / 27.5)
        self.assertAlmostEqual(upstream, 144.0, places=6)
        self.assertAlmostEqual(midi_pitch_to_contour_bin(69),
                               upstream + 1.0, places=6)

    def test_pure_tone_bends_zero_with_correction(self):
        output = self._synthetic()
        notes = model_output_to_notes(output)
        self.assertEqual(len(notes), 1)
        self.assertTrue(all(b == 0 for b in notes[0][4]),
                        f"bends {notes[0][4]}")

    def test_bends_follow_contour_peak(self):
        # A contour peak 2 bins above the corrected bin bends +2.
        output = self._synthetic(contour_bin=147)
        notes = model_output_to_notes(output)
        self.assertEqual(len(notes), 1)
        self.assertTrue(all(b == 2 for b in notes[0][4]),
                        f"bends {notes[0][4]}")

    def test_short_blip_produces_no_note(self):
        output = self._synthetic(note_frames=(10, 15))
        self.assertEqual(model_output_to_notes(output), [])

    def test_silence_produces_no_notes(self):
        output = {"note": np.zeros((200, 88)),
                  "onset": np.zeros((200, 88)),
                  "contour": np.zeros((200, 264))}
        self.assertEqual(model_output_to_notes(output), [])

    def test_get_pitch_bends_uses_corrected_mapping(self):
        contours = np.zeros((50, 264))
        contours[:, 145] = 0.8
        bends = get_pitch_bends(contours, [(0, 50, 69, 0.9)])
        self.assertEqual(len(bends), 1)
        self.assertTrue(all(b == 0 for b in bends[0][4]))


class TestMidiBends(unittest.TestCase):
    def test_bend_write_read_round_trip(self):
        notes = [{"pitch": 69, "start": 0, "duration": 960, "velocity": 100,
                  "bends": [(0, 8192), (480, 8192 + 4096 // 3), (960, 8192)]}]
        data = write_midi(notes)
        # 0xE0 also appears in the header (division 480 = 0x01E0) —
        # check the track data only.
        self.assertIn(b"\xe0", data[data.find(b"MTrk") + 8:])
        back = read_midi(data)
        self.assertEqual(len(back["notes"]), 1)
        note = back["notes"][0]
        self.assertEqual((note["pitch"], note["start"], note["duration"],
                          note["velocity"]), (69, 0, 960, 100))

    def test_bend_value_decodes(self):
        notes = [{"pitch": 69, "start": 0, "duration": 480, "velocity": 100,
                  "bends": [(0, 8192 + 1365)]}]
        data = write_midi(notes)
        # 0xE0 also appears in the header (division 480 = 0x01E0) —
        # search the track data only.
        track = data[data.find(b"MTrk") + 8:]
        pos = track.find(b"\xe0")
        self.assertGreater(pos, 0)
        lsb, msb = track[pos + 1], track[pos + 2]
        self.assertEqual(lsb | (msb << 7), 8192 + 1365)

    def test_bend_out_of_range_refused(self):
        notes = [{"pitch": 69, "start": 0, "duration": 480, "velocity": 100,
                  "bends": [(0, 20000)]}]
        with self.assertRaises(ValueError):
            write_midi(notes)

    def test_notes_without_bends_unchanged(self):
        notes = [{"pitch": 60, "start": 0, "duration": 480}]
        data = write_midi(notes)
        back = read_midi(data)
        self.assertEqual(len(back["notes"]), 1)
        # 0xE0 also appears in the header (division 480 = 0x01E0) —
        # check the track data only.
        track = data[data.find(b"MTrk") + 8:]
        self.assertNotIn(b"\xe0", track)


class TestRegistrationAndGates(unittest.TestCase):
    def test_tools_registered_with_properties(self):
        decls = {d["name"]: d for d in _registry.get_declarations()}
        self.assertIn("audio_to_midi", decls)
        self.assertIn("audio_to_midi_status", decls)
        props = list(decls["audio_to_midi"]["parameters"]["properties"])
        self.assertEqual(props, ["audio_path", "output", "onset_threshold",
                                 "frame_threshold", "min_note_length_ms",
                                 "tempo_bpm"])
        self.assertEqual(
            decls["audio_to_midi"]["parameters"]["required"], ["audio_path"])
        self.assertIs(
            decls["audio_to_midi"]["parameters"].get("additionalProperties"),
            False)
        self.assertIs(
            decls["audio_to_midi_status"]["parameters"]
            .get("additionalProperties"), False)
        self.assertEqual(
            decls["audio_to_midi_status"]["parameters"]["properties"], {})

    def test_confirm_gate(self):
        self.assertIn("audio_to_midi", _CONFIRM_GATE_TOOLS)
        self.assertNotIn("audio_to_midi_status", _CONFIRM_GATE_TOOLS)

    def test_free_gate(self):
        # Confirm-gated like generate_melody, NOT free-by-design:
        # the tree forbids a tool being both (test_classification
        # enforces the disjoint invariant), and a model-chosen
        # output path is exactly what the confirm gate judges.
        self.assertNotIn("audio_to_midi", _FREE_BY_DESIGN)
        self.assertNotIn("audio_to_midi_status", _FREE_BY_DESIGN)

    def test_path_gates(self):
        self.assertEqual(_FILE_READ_PATH_ARGS.get("audio_to_midi"),
                         ("audio_path",))
        self.assertEqual(_WRITE_DESTINATION_ARGS.get("audio_to_midi"),
                         ("output",))
        self.assertEqual(_FOLDER_SCOPE_PATH_ARGS.get("audio_to_midi"),
                         ("audio_path", "output"))

    def test_catalogue_grouping(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        members = GROUP_MEMBERS.get("Play & create", ())
        self.assertIn("audio_to_midi", members)
        self.assertIn("audio_to_midi_status", members)

    def test_snapshot_regenerated(self):
        from core.catalogue_snapshot import GROUP_MEMBERS
        flat = [n for members in GROUP_MEMBERS.values() for n in members]
        self.assertEqual(len(flat), 754)  # INTENTIONAL — 688 -> 690: fund_returns, mutual_fund_nav grouped in Everyday skills (R8-8 mftool port, 2026-11-10); R8-9 pypresence adoption (2026-11-10): 690 -> 692 — discord_presence + set_discord_client_id grouped in System & apps; R9-2 rustic adoption (2026-11-10): 692 -> 697 — five rustic tools grouped in Files & media; R9-3 whoosh port (2026-11-10): 697 -> 700 — index_files, search_indexed_files, disk_index_status grouped in Files & media; R9-4 mutagen port (2026-11-10): 700 -> 702 — sync_folders_two_way, two_way_sync_status grouped in Files & media; R9-8 elodie port (2026-11-10): 702 -> 704 — organise_photos, preview_photo_organisation grouped in Files & media; R10-1 chrome-devtools-mcp port (2026-11-10): 704 -> 707 — browser_console, browser_network, browser_performance grouped in Web; R10-3 browser-history port (2026-11-10): 707 -> 709 — browser_history, browser_bookmarks grouped in Web; R10-4 python-adblock port (2026-11-10): 709 -> 711 — check_url_blocked, filter_list_status grouped in Web; R10-5 python-readability port (2026-11-10): 711 -> 712 — extract_article grouped in Web; R11-1 ollama port (2026-11-10): 712 -> 715 — ollama_status, ollama_pull, ollama_delete grouped in System & apps; R16-8 event-planning pack (2026-11-11): 715 -> 721 — six tools grouped in Everyday skills; R16-9 attic pack (2026-11-11): 721 -> 722 — home_inventory grouped in Everyday skills; R16-10 GreaterWMS pack (2026-11-11): 722 -> 723 — warehouse_stock grouped in Your world; R17-1 bookrail pack (2026-11-11): 723 -> 724 — bookings grouped in Your world; R17-2 trivy pack (2026-11-11): 724 -> 725 — security_scan grouped in System & apps; R17-3 go2rtc pack (2026-11-11): 725 -> 726 — ip_camera grouped in Hands & eyes; R17-4 MCSManager pack (2026-11-11): 726 -> 727 — game_server grouped in Hands & eyes; R17-5 komga pack (2026-11-11): 727 -> 728 — ebook_library grouped in Files & media; R17-6 openstays pack (2026-11-11): 728 -> 729 — lodging grouped in Your world; R17-7 qlcplus pack (2026-11-12): 729 -> 730 — stage_lighting grouped in Hands & eyes; R17-8 pvlib pack (2026-11-12): 730 -> 731 — solar_yield grouped in Your world; R17-9 wardrobe pack (2026-11-12): 731 -> 732 — wardrobe grouped in Your world; R18-1 stripe pack (2026-11-12): 732 -> 733 — billing grouped in Your world; R18-3 mcp-context-forge pack (2026-11-12): 733 -> 734 — mcp_registry grouped in Mind; R18-4 a2a pack (2026-11-12): 734 -> 735 — agent_call grouped in Mind; R18-5 dspy pack (2026-11-12): 735 -> 736 — prompt_tune grouped in Mind; R18-6 fonoster pack (2026-11-12): 736 -> 737 — voice_agent grouped in Your world; R18-7 attendize pack (2026-11-12): 737 -> 738 — event_tickets grouped in Everyday skills; R18-8 ta pack (2026-11-12): 738 -> 739 — technical_analysis grouped in Your world; R18-10 timetrace pack (2026-11-12): 739 -> 740 — time_tracker grouped in Everyday skills; R19-1 videolingo pack (2026-11-12): 740 -> 741 — audio_localise grouped in Play & create; R19-2 ortools pack (2026-11-12): 741 -> 742 — optimise grouped in Everyday skills; R19-3 aureuserp pack (2026-11-12): 742 -> 743 — erp grouped in Everyday skills; R19-4 vidbee pack (2026-11-12): 743 -> 744 — media_chat grouped in Play & create; R19-5 invoify pack (2026-11-12): 744 -> 745 — invoice grouped in Everyday skills; R19-6 tolgee pack (2026-11-12): 745 -> 746 — translations grouped in Everyday skills; R19-7 spoolman pack (2026-11-12): 746 -> 747 — filament grouped in Everyday skills; R19-8 djangocrm pack (2026-11-12): 747 -> 748 — deals grouped in Everyday skills; R19-10 vroom pack (2026-11-12): 748 -> 749 — vehicle_routes grouped in Everyday skills; GAPS-7 connectors pack (2026-11-13): 749 -> 750 — sync_updates grouped in Your world; SOCIAL Phase 1 (2026-11-13): 750 -> 751 — social_send grouped in Your world; GAPS-8 scheduled-agents pack (2026-11-13): 751 -> 752 — scheduled_agents grouped in Your world; field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan
        self.assertIn("audio_to_midi", flat)


class TestHonestRefusals(unittest.TestCase):
    def test_missing_file(self):
        result = audio_to_midi(audio_path="/no/such/file.wav")
        self.assertIn("couldn't find", result)

    def test_non_wav_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "song.mp3")
            pathlib.Path(path).write_bytes(b"ID3" + b"\x00" * 100)
            result = audio_to_midi(audio_path=path)
            self.assertIn("not a WAV file", result)

    def test_bad_thresholds_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tone.wav")
            _write_wav(path, _sine(440, 0.5, 22050), 22050)
            self.assertIn("onset threshold",
                          audio_to_midi(audio_path=path, onset_threshold=2.0))
            self.assertIn("frame threshold",
                          audio_to_midi(audio_path=path, frame_threshold=0.0))
            self.assertIn("minimum note length",
                          audio_to_midi(audio_path=path,
                                        min_note_length_ms=5.0))
            self.assertIn("tempo",
                          audio_to_midi(audio_path=path, tempo_bpm=300.0))

    def test_no_model_or_runtime_refusal_is_plain_words(self):
        if basic_pitch.model_ready():
            self.skipTest("model is placed — the refusal path does not apply")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tone.wav")
            _write_wav(path, _sine(440, 0.5, 22050), 22050)
            result = audio_to_midi(audio_path=path)
            self.assertIsInstance(result, str)
            self.assertNotIn("Traceback", result)
            lowered = result.lower()
            self.assertTrue("model" in lowered or "onnxruntime" in lowered,
                            result)

    def test_status_names_the_model_path(self):
        status = audio_to_midi_status()
        self.assertIn("nmp.onnx", status)
        self.assertIn(str(basic_pitch.model_path()), status)

    def test_model_status_honest_when_absent(self):
        if basic_pitch.model_ready():
            self.skipTest("model is placed — the absent path does not apply")
        self.assertIn("not placed", model_status())

    def test_unwritable_output_refused_plainly(self):
        # The write path is exercised with a stubbed transcribe so
        # the test does not need the model or the runtime.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tone.wav")
            _write_wav(path, _sine(440, 0.5, 22050), 22050)
            fake_notes = [(0.0, 1.0, 69, 0.9, [0])]
            with unittest.mock.patch.object(
                    basic_pitch, "transcribe", return_value=fake_notes):
                result = audio_to_midi(audio_path=path,
                                       output="/proc/nope/out.mid")
            self.assertIn("Couldn't save the MIDI", result)

    def test_stubbed_transcribe_writes_readable_midi(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tone.wav")
            _write_wav(path, _sine(440, 0.5, 22050), 22050)
            out = os.path.join(tmp, "tone.mid")
            fake_notes = [(0.0, 1.0, 69, 0.9, [0])]
            with unittest.mock.patch.object(
                    basic_pitch, "transcribe", return_value=fake_notes):
                result = audio_to_midi(audio_path=path, output=out)
            self.assertIn("1 notes", result, result)
            back = read_midi(pathlib.Path(out).read_bytes())
            self.assertEqual(len(back["notes"]), 1)
            self.assertEqual(back["notes"][0]["pitch"], 69)
            track = pathlib.Path(out).read_bytes()
            self.assertIn(b"\xe0", track[track.find(b"MTrk") + 8:])


@unittest.skipUnless(_real_model_available(),
                     "real model test: needs onnxruntime + the lab model file")
class TestRealModel(unittest.TestCase):
    def setUp(self):
        self._old_dir = basic_pitch.MODEL_DIR
        basic_pitch.MODEL_DIR = LAB_MODEL.parent
        self.addCleanup(setattr, basic_pitch, "MODEL_DIR", self._old_dir)
        self.addCleanup(setattr, basic_pitch, "_session", None)

    def test_two_tone_transcription(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = np.concatenate([_sine(440, 3.0, 22050),
                                    _sine(523.25, 3.0, 22050)])
            path = os.path.join(tmp, "two_tone.wav")
            _write_wav(path, audio, 22050)
            out = os.path.join(tmp, "two_tone.mid")
            result = audio_to_midi(audio_path=path, output=out)
            self.assertIn("2 notes", result, result)
            back = read_midi(pathlib.Path(out).read_bytes())
            pitches = {n["pitch"] for n in back["notes"]}
            self.assertEqual(pitches, {69, 72})
            self.assertEqual(len(back["notes"]), 2)

    def test_pure_tone_bends_zero_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a4.wav")
            _write_wav(path, _sine(440, 3.0, 22050), 22050)
            notes = basic_pitch.transcribe(path)
            self.assertEqual(len(notes), 1)
            _s, _e, pitch, _a, bends = notes[0]
            self.assertEqual(pitch, 69)
            self.assertTrue(all(b == 0 for b in bends), f"bends {bends}")

    def test_silence_transcribes_to_no_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "silence.wav")
            _write_wav(path, np.zeros(int(2.0 * 22050), dtype=np.float32),
                       22050)
            out = os.path.join(tmp, "silence.mid")
            result = audio_to_midi(audio_path=path, output=out)
            self.assertIn("No notes came out", result)
            back = read_midi(pathlib.Path(out).read_bytes())
            self.assertEqual(back["notes"], [])


if __name__ == "__main__":
    unittest.main()
