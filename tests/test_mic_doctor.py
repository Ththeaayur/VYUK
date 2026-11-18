"""Mic doctor verdicts (v6.3.x mic remake) — the PURE
diagnose() matrix + classify_stream_flags, testable without
a microphone.

What this pins (the doctor-audit findings):
  * the meter dead band (0.002–0.02) used to report "ok" —
    a quiet-but-present meter was reported as healthy while
    VYUK heard nothing. Now: stream hearing → ok (quiet
    voice); stream not hearing → "inconclusive", honestly.
  * the stream_fault verdict was unreachable — run_diagnosis
    always accepted stream_note but the doctor never passed
    it. Now the probe's classified note flows through.
  * classify_stream_flags' branches (each flag shape maps
    to one plain-words note).
"""

import sys
import unittest
from pathlib import Path

TREE_ROOT = Path(__file__).resolve().parent.parent
if str(TREE_ROOT) not in sys.path:
    sys.path.insert(0, str(TREE_ROOT))
if str(TREE_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(TREE_ROOT.parent))

from core.voice import mic_doctor as md


class DiagnoseTests(unittest.TestCase):
    def _endpoint(self, state="active"):
        return {"state_name": state}

    def _volume(self, mute=False, level=0.5):
        return {"mute": mute, "level": level}

    def test_meter_moves_but_stream_deaf_is_app_side(self):
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=0.05, stream_hearing=False)
        self.assertEqual(d["verdict"], "app_side")
        self.assertIn("Sound IS reaching Windows", d["sentence"])

    def test_meter_flat_is_hardware_or_driver(self):
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=0.001, stream_hearing=False)
        self.assertEqual(d["verdict"], "hardware_or_driver")

    def test_dead_band_stream_hearing_is_ok_quiet(self):
        # Meter moved a little AND the stream heard something:
        # quiet voice, not a fault.
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=0.01, stream_hearing=True)
        self.assertEqual(d["verdict"], "ok")
        self.assertIn("quiet", d["sentence"])

    def test_dead_band_stream_deaf_is_inconclusive(self):
        # Meter moved a little and the stream heard nothing:
        # the old code reported "ok" here — a quiet-but-
        # present meter read as healthy while VYUK heard
        # nothing. Now it says so honestly.
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=0.01, stream_hearing=False)
        self.assertEqual(d["verdict"], "inconclusive")
        self.assertIn("too little to tell", d["sentence"])

    def test_dead_band_stream_unknown_is_inconclusive(self):
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=0.01, stream_hearing=None)
        self.assertEqual(d["verdict"], "inconclusive")

    def test_dead_band_boundaries(self):
        # Exactly at METER_FLAT: hardware_or_driver (<=).
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=md.METER_FLAT, stream_hearing=False)
        self.assertEqual(d["verdict"], "hardware_or_driver")
        # Exactly at METER_MOVES with a deaf stream: app_side.
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=md.METER_MOVES, stream_hearing=False)
        self.assertEqual(d["verdict"], "app_side")
        # Just above FLAT, stream hearing: ok (quiet).
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        meter_peak=md.METER_FLAT + 0.0001,
                        stream_hearing=True)
        self.assertEqual(d["verdict"], "ok")

    def test_endpoint_states(self):
        for state, verdict in (("disabled", "endpoint_disabled"),
                               ("not present", "endpoint_not_present"),
                               ("unplugged", "endpoint_unplugged")):
            d = md.diagnose(endpoint=self._endpoint(state),
                            volume=self._volume())
            self.assertEqual(d["verdict"], verdict, state)

    def test_os_muted_and_level_zero(self):
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(mute=True))
        self.assertEqual(d["verdict"], "os_muted")
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(level=0.0))
        self.assertEqual(d["verdict"], "level_zero")

    def test_stream_fault_reachable_without_meter(self):
        # The meter leg did not run, but the probe's stream
        # reported a classified fault — the verdict the doctor
        # could never reach before the remake wired
        # stream_note through.
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume(),
                        stream_note="the stream reports input "
                        "overflow (the PC is not draining the "
                        "microphone fast enough — buffers are "
                        "being lost)")
        self.assertEqual(d["verdict"], "stream_fault")
        self.assertIn("overflow", d["sentence"])

    def test_settings_ok_unprobed_and_unprobed(self):
        d = md.diagnose(endpoint=self._endpoint(),
                        volume=self._volume())
        self.assertEqual(d["verdict"], "settings_ok_unprobed")
        d = md.diagnose()
        self.assertEqual(d["verdict"], "unprobed")


class ClassifyStreamFlagsTests(unittest.TestCase):
    def test_each_flag_maps_to_one_note(self):
        cases = (
            ({"silent_buffer": True}, "silent buffers"),
            ({"timestamp_error": True}, "timestamps are erroring"),
            ({"discontinuity": True}, "discontinuities"),
            ({"input_overflow": True}, "input overflow"),
            ({"input_underflow": True}, "input underflow"),
        )
        for flags, needle in cases:
            note = md.classify_stream_flags(flags)
            self.assertIsNotNone(note, flags)
            self.assertIn(needle, note)

    def test_no_flags_is_none(self):
        self.assertIsNone(md.classify_stream_flags(None))
        self.assertIsNone(md.classify_stream_flags({}))
        self.assertIsNone(md.classify_stream_flags(
            {"input_overflow": False, "input_underflow": False}))


class RunDiagnosisTests(unittest.TestCase):
    def test_run_diagnosis_forwards_stream_note(self):
        # Off Windows the mmdevice probes are unavailable, so
        # the verdict rests on the stream note alone — the
        # path the doctor can now reach.
        d = md.run_diagnosis(stream_note="the stream reports "
                             "input underflow (the device is not "
                             "delivering buffers on time)")
        self.assertEqual(d["verdict"], "stream_fault")
        self.assertIn("underflow", d["sentence"])

    def test_run_diagnosis_unprobed_without_probes(self):
        d = md.run_diagnosis()
        self.assertEqual(d["verdict"], "unprobed")


if __name__ == "__main__":
    unittest.main()
