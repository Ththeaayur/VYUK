"""B-R1-9: Kenney CC0 audition bundle.

The six Kenney candidates are AUDITION MATERIAL ONLY — they are
played on explicit demand from the Settings audition row, never
by the assistant, and the earcon veto still forbids layering
them into product audio. These tests pin that boundary:
* the bundle is present and decodes cleanly (WAV contract),
* the licence log clears all six and records the veto pending,
* mute / missing file / missing log / unknown name are honest
  refusals, never substitutes,
* attempt_layer still refuses (veto intact),
* the shipped earcon set is untouched.
QtMultimedia is absent from the lab Qt venv, so playback tests
are split: the no-backend refusal is asserted here, and the
played-True path is skipped unless QSoundEffect imports.
"""
import json
import math
import shutil
import tempfile
import unittest
import wave
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
AUDITION_DIR = TREE / "gui" / "assets" / "audition" / "kenney"

from gui.audition import (  # noqa: E402
    AUDITION_SOUNDS,
    KenneyAudition,
    TARGET_RMS_DBFS,
    match_gain_dbfs,
    match_gain_factor,
    _AVAILABLE as AUDITION_BACKEND_AVAILABLE,
)
from gui.sounds import CUE_NAMES, Earcons  # noqa: E402
from core import sound_pools_v564 as pools  # noqa: E402

EXPECTED = {
    # name: (filename, moment, pack, rms_dbfs, duration_sec, channels)
    "forceField_000": ("kenney_forceField_000.wav", "boot", "Kenney Sci-Fi Sounds", -11.53, 0.954, 1),
    "forceField_001": ("kenney_forceField_001.wav", "boot", "Kenney Sci-Fi Sounds", -11.59, 0.953, 1),
    "twoTone1": ("kenney_twoTone1.wav", "notification", "Kenney Digital Audio", -14.49, 0.721, 1),
    "twoTone2": ("kenney_twoTone2.wav", "notification", "Kenney Digital Audio", -15.03, 0.728, 1),
    "click_001": ("kenney_click_001.wav", "orb_tap", "Kenney Interface Sounds", -26.40, 0.097, 1),
    "click1": ("kenney_click1.wav", "orb_tap", "Kenney UI Audio", -22.46, 0.091, 2),
}


def _rms_dbfs(path):
    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        channels = wav.getnchannels()
    import array as _array

    samples = _array.array("h")
    samples.frombytes(frames)
    if channels > 1:
        samples = samples[::channels]
    ms = sum(int(s) ** 2 for s in samples) / len(samples)
    return 20.0 * math.log10(math.sqrt(ms) / 32768.0)


class TestKenneyBundle(unittest.TestCase):
    def test_all_six_files_present(self):
        for name, (filename, _m, _p, _r, _d, _c) in EXPECTED.items():
            self.assertIn(name, AUDITION_SOUNDS)
            self.assertTrue((AUDITION_DIR / filename).is_file(), filename)
        self.assertTrue((AUDITION_DIR / "BUNDLE_RECORD.md").is_file())
        self.assertTrue((AUDITION_DIR / "licence_log.json").is_file())

    def test_wavs_decode_cleanly_and_match_record(self):
        for name, (filename, _m, _p, rms, dur, channels) in EXPECTED.items():
            path = AUDITION_DIR / filename
            with wave.open(str(path), "rb") as wav:
                self.assertEqual(wav.getsampwidth(), 2, filename)
                self.assertEqual(wav.getframerate(), 44100, filename)
                self.assertEqual(wav.getnchannels(), channels, filename)
                frames = wav.readframes(wav.getnframes())
                got_dur = wav.getnframes() / 44100.0
            self.assertAlmostEqual(got_dur, dur, delta=0.02, msg=filename)
            import array as _array

            samples = _array.array("h")
            samples.frombytes(frames)
            peak = max(abs(int(s)) for s in samples) / 32768.0
            self.assertLess(peak, 1.0, filename)
            self.assertAlmostEqual(_rms_dbfs(path), rms, delta=0.5, msg=filename)

    def test_table_moments_and_packs(self):
        for name, (filename, moment, pack, rms, _d, _c) in EXPECTED.items():
            entry = AUDITION_SOUNDS[name]
            self.assertEqual(entry[0], filename)
            self.assertEqual(entry[1], moment)
            self.assertEqual(entry[2], pack)
            self.assertAlmostEqual(entry[3], rms, places=2)

    def test_target_is_shipped_standard(self):
        self.assertEqual(TARGET_RMS_DBFS, -20.0)

    def test_gain_math(self):
        # A file 8.47 dB louder than the standard gets -8.47 dB.
        self.assertAlmostEqual(match_gain_dbfs(-11.53), -8.47, places=2)
        # A file 6.40 dB quieter gets +6.40 dB.
        self.assertAlmostEqual(match_gain_dbfs(-26.40), 6.40, places=2)
        self.assertAlmostEqual(
            match_gain_factor(-11.53), 10.0 ** (-8.47 / 20.0), places=4)

    def test_play_volume_clamps(self):
        player = KenneyAudition(volume=1.0, enabled=True)
        # The loudest file (-11.53) at full volume: 1.0 * 0.377 < 1.
        self.assertLess(player._play_volume(-11.53), 1.0)
        # The quietest file (-26.40) at full volume would exceed 1.0.
        self.assertEqual(player._play_volume(-26.40), 1.0)


class TestKenneyLicenceLog(unittest.TestCase):
    def test_all_six_cleared_and_veto_recorded(self):
        log = json.loads((AUDITION_DIR / "licence_log.json").read_text(encoding="utf-8"))
        self.assertTrue(log["earcon_veto_pending"])
        self.assertEqual(len(log["entries"]), 6)
        for entry in log["entries"]:
            self.assertEqual(entry["verdict"], "cleared", entry["record"]["asset"])
            self.assertFalse(entry["record"]["loudness_matched"])

    def test_every_entry_names_its_pack(self):
        log = json.loads((AUDITION_DIR / "licence_log.json").read_text(encoding="utf-8"))
        for entry in log["entries"]:
            url = entry["record"]["source_url"]
            self.assertTrue(url.startswith("https://kenney.nl/assets/"), url)
            self.assertEqual(entry["record"]["quarry"], "kenney")
            self.assertEqual(entry["record"]["licence"], "CC0 1.0")
            self.assertTrue(entry["record"]["verified_from_primary"])


class TestKenneyAuditionBehaviour(unittest.TestCase):
    def test_mute_suppresses_playback(self):
        player = KenneyAudition(volume=0.5, enabled=False)
        record = player.play("twoTone1")
        self.assertFalse(record["played"])
        self.assertIn("off", record["reason"])

    def test_unknown_name_is_honest(self):
        player = KenneyAudition(volume=0.5, enabled=True)
        record = player.play("nope")
        self.assertFalse(record["played"])
        self.assertIn("unknown candidate", record["reason"])

    def test_missing_file_is_honest_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Licence log present (cleared) but no WAVs at all.
            shutil.copy(AUDITION_DIR / "licence_log.json", tmp)
            player = KenneyAudition(volume=0.5, enabled=True,
                                    base_dir=Path(tmp))
            self.assertTrue(player.is_cleared("twoTone1"))
            record = player.play("twoTone1")
            self.assertFalse(record["played"])
            self.assertIn("kenney_twoTone1.wav", record["reason"])
            self.assertIn("missing", record["reason"])

    def test_missing_log_refuses_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            player = KenneyAudition(volume=0.5, enabled=True,
                                    base_dir=Path(tmp))
            for name in AUDITION_SOUNDS:
                self.assertFalse(player.is_cleared(name))
                self.assertFalse(player.play(name)["played"])

    @unittest.skipUnless(AUDITION_BACKEND_AVAILABLE,
                         "QtMultimedia is absent from the lab Qt venv")
    def test_play_returns_played_when_backend_present(self):
        player = KenneyAudition(volume=0.5, enabled=True)
        record = player.play("twoTone1")
        self.assertTrue(record["played"], record)
        self.assertAlmostEqual(record["gain_db"], -5.51, places=2)

    @unittest.skipIf(AUDITION_BACKEND_AVAILABLE,
                     "backend present — the no-backend refusal does not apply")
    def test_no_backend_is_honest_failure(self):
        player = KenneyAudition(volume=0.5, enabled=True)
        self.assertTrue(player.is_cleared("twoTone1"))
        record = player.play("twoTone1")
        self.assertFalse(record["played"])
        self.assertIn("no audio backend", record["reason"])

    def test_candidates_table_reports_clearance(self):
        player = KenneyAudition(volume=0.5, enabled=True)
        rows = {row["name"]: row for row in player.candidates()}
        self.assertEqual(len(rows), 6)
        for name in AUDITION_SOUNDS:
            self.assertTrue(rows[name]["cleared"], name)
            self.assertEqual(rows[name]["moment"], AUDITION_SOUNDS[name][1])


class TestKenneyVetoIntact(unittest.TestCase):
    def test_attempt_layer_still_refuses(self):
        record = pools.AssetLicenceRecord(
            quarry="kenney", asset="kenney_twoTone1.wav",
            licence="CC0 1.0",
            source_url="https://kenney.nl/assets/digital-audio",
            verified_from_primary=True, loudness_matched=False)
        verdict, _reason = pools.validate_record(record)
        self.assertEqual(verdict, "cleared")
        result = pools.attempt_layer(record, cue="alert")
        self.assertFalse(result["layered"])
        self.assertTrue(result["refused"])
        self.assertIsNone(result["produced_audio"])

    def test_veto_flag_still_pending(self):
        self.assertTrue(pools.EARCON_VETO_PENDING)

    def test_audition_module_never_layers(self):
        src = (TREE / "gui" / "audition.py").read_text(encoding="utf-8")
        # The module docstring NAMES the veto gate; the code must
        # never call it. Check the code body, not the docstring.
        code = src.split('"""', 2)[2]
        self.assertNotIn("attempt_layer", code)
        self.assertNotIn("Earcons(", code)
        self.assertNotIn("gui.sounds", code)
        self.assertNotIn("enable_always_on_bed", code)


class TestShippedEarconsUntouched(unittest.TestCase):
    def test_cue_names_unchanged(self):
        self.assertEqual(CUE_NAMES, (
            "wake", "ack", "sent", "done", "error",
            "connect", "disconnect", "ready", "capture", "alert",
            "lock", "start", "step", "handback", "toast",
        ))

    def test_shipped_wavs_both_families_present(self):
        for cue in CUE_NAMES:
            for sub in ("", "earcons_soft"):
                path = TREE / "gui" / "assets" / sub / (cue + ".wav") \
                    if sub else TREE / "gui" / "assets" / (cue + ".wav")
                self.assertTrue(path.is_file(), str(path))


class TestKenneySettingsWiring(unittest.TestCase):
    def test_settings_page_has_audition_row(self):
        src = (TREE / "gui" / "window_settings_voice.py").read_text(encoding="utf-8")
        self.assertIn("audition_buttons", src)
        self.assertIn("_on_audition_kenney", src)
        self.assertIn("from voice_assistant.gui.audition import AUDITION_SOUNDS", src)
        self.assertIn("lbl_audition", src)
        # No setStyleSheet CALL added by this batch (audit2s1 pins
        # the total): the two calls are the pre-existing ones
        # (pronunciation input + theme swatch). The comment above
        # the audition row mentions the name but adds no call.
        self.assertEqual(src.count(".setStyleSheet("), 2)

    def test_audition_row_uses_no_live_emission_path(self):
        src = (TREE / "gui" / "window_settings_voice.py").read_text(encoding="utf-8")
        self.assertNotIn("self.earcons._play", src)


if __name__ == "__main__":
    unittest.main()
