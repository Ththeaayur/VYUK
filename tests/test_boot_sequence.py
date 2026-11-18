"""B-R1-4 tests: the adewaskar boot sequence.

Every stage is driven by a fake check — no real mic, no real
audio, no engine import. The real status calls are covered
only by their shape: a check that returns a verdict dict is
reported as-is; a check that raises is reported as failed.
"""

import datetime
import unittest

from core.desktop import boot_sequence as bs


def _fixed_clock(hour):
    return lambda: datetime.datetime(2026, 11, 9, hour, 0, 0)


def _fake_stages(results):
    """Stages whose checks return the given verdict dicts, in
    order. ``results`` is a list of (verdict, reason)."""
    stages = []
    for i, (verdict, reason) in enumerate(results):
        def _check(v=verdict, r=reason):
            return {"verdict": v, "reason": r, "checked": True}
        stages.append({"id": f"stage-{i}", "label": f"Stage {i}",
                       "beat": "suit", "check": _check})
    return stages


class TestBootSequence(unittest.TestCase):
    def test_checked_stage_reports_its_real_verdict(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", ""),
                                 ("degraded", "voice down")]),
            clock=_fixed_clock(9))
        first = seq.advance()
        self.assertEqual(first["verdict"], "ready")
        self.assertTrue(first["checked"])
        second = seq.advance()
        self.assertEqual(second["verdict"], "degraded")
        self.assertEqual(second["reason"], "voice down")
        self.assertTrue(seq.complete)

    def test_unchecked_stage_is_never_reported_ready(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", ""), ("ready", ""),
                                 ("ready", "")]),
            clock=_fixed_clock(9))
        seq.advance()
        report = seq.report()
        self.assertFalse(report["complete"])
        self.assertFalse(report["all_ready"])
        self.assertEqual(report["stages"][0]["verdict"], "ready")
        self.assertEqual(report["stages"][1]["verdict"], "unchecked")
        self.assertEqual(report["stages"][2]["verdict"], "unchecked")
        # The greeting names only the checked stage as ready.
        self.assertIn("Stage 0", report["greeting"])
        self.assertNotIn("Stage 1 is ready", report["greeting"])
        self.assertNotIn("Stage 2 is ready", report["greeting"])

    def test_failed_check_is_reported_honestly_and_sequence_continues(self):
        def _boom():
            raise RuntimeError("the check itself broke")

        def _ok():
            return {"verdict": "ready", "reason": "", "checked": True}

        seq = bs.BootSequence(stages=[
            {"id": "boom", "label": "Boom", "beat": "suit",
             "check": _boom},
            {"id": "ok", "label": "Ok", "beat": "suit", "check": _ok},
        ], clock=_fixed_clock(9))
        first = seq.advance()
        self.assertEqual(first["verdict"], "failed")
        self.assertIn("RuntimeError", first["reason"])
        self.assertIn("the check itself broke", first["reason"])
        second = seq.advance()
        self.assertEqual(second["verdict"], "ready")
        self.assertTrue(seq.complete)
        report = seq.report()
        self.assertFalse(report["all_ready"])
        self.assertIn("Boom is down", report["greeting"])

    def test_check_returning_no_verdict_is_a_failure(self):
        def _no_verdict():
            return {"reason": "nothing here"}

        seq = bs.BootSequence(stages=[
            {"id": "x", "label": "X", "beat": "suit",
             "check": _no_verdict},
        ], clock=_fixed_clock(9))
        out = seq.advance()
        self.assertEqual(out["verdict"], "failed")

    def test_check_returning_unknown_verdict_is_a_failure(self):
        def _weird():
            return {"verdict": "sparkling", "reason": "", "checked": True}

        seq = bs.BootSequence(stages=[
            {"id": "x", "label": "X", "beat": "suit", "check": _weird},
        ], clock=_fixed_clock(9))
        out = seq.advance()
        self.assertEqual(out["verdict"], "failed")

    def test_skip_ends_cleanly_and_records_it(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", ""), ("ready", ""),
                                 ("ready", "")]),
            clock=_fixed_clock(9))
        seq.advance()
        report = seq.skip()
        self.assertTrue(report["skipped"])
        self.assertTrue(report["complete"])
        self.assertFalse(report["all_ready"])
        self.assertEqual(report["stages"][0]["verdict"], "ready")
        self.assertEqual(report["stages"][1]["verdict"], "skipped")
        self.assertEqual(report["stages"][2]["verdict"], "skipped")
        self.assertIn("boot skipped", report["greeting"])
        self.assertIn("Stage 0", report["greeting"])
        # Advancing after a skip returns None — no wedge.
        self.assertIsNone(seq.advance())

    def test_skip_before_any_check_records_nothing_checked(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", ""), ("ready", "")]),
            clock=_fixed_clock(9))
        report = seq.skip()
        self.assertTrue(report["skipped"])
        self.assertIn("boot skipped before any check", report["greeting"])

    def test_advance_runs_exactly_one_stage_per_call(self):
        # The boot never blocks the first reply: the driver
        # ticks it, one stage per call, and the rest stay
        # unchecked until their turn.
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", "")] * 4),
            clock=_fixed_clock(9))
        seq.advance()
        seq.advance()
        checked = [s for s in seq.report()["stages"]
                   if s["checked"]]
        self.assertEqual(len(checked), 2)
        self.assertEqual(seq.pending, 2)
        self.assertFalse(seq.complete)

    def test_run_all_completes_and_reports_all_ready(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", "")] * 3),
            clock=_fixed_clock(9))
        report = seq.run_all()
        self.assertTrue(report["complete"])
        self.assertTrue(report["all_ready"])
        self.assertIn("all", report["greeting"].lower())
        self.assertEqual(len(report["log"]), 4)  # header + 3 stages

    def test_greeting_names_only_what_was_checked(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", ""),
                                 ("degraded", "voice down"),
                                 ("failed", "mic broke")]),
            clock=_fixed_clock(15))
        seq.run_all()
        greeting = seq.report()["greeting"]
        self.assertTrue(greeting.startswith("Good afternoon"))
        self.assertIn("Stage 0 is ready", greeting)
        self.assertIn("Stage 1 is down (voice down)", greeting)
        self.assertIn("Stage 2 is down (mic broke)", greeting)

    def test_greeting_time_of_day(self):
        for hour, word in ((8, "Good morning"), (13, "Good afternoon"),
                           (20, "Good evening")):
            seq = bs.BootSequence(
                stages=_fake_stages([("ready", "")]),
                clock=_fixed_clock(hour))
            seq.run_all()
            self.assertTrue(seq.report()["greeting"].startswith(word),
                            hour)

    def test_greeting_with_name(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", "")]),
            name="Aayur", clock=_fixed_clock(9))
        seq.run_all()
        self.assertTrue(
            seq.report()["greeting"].startswith("Good morning, Aayur"))

    def test_greeting_before_any_run(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", "")]),
            clock=_fixed_clock(9))
        self.assertIn("boot has not run yet", seq.report()["greeting"])

    def test_non_dict_supplies_is_refused(self):
        with self.assertRaises(ValueError):
            bs.BootSequence(supplies="nope")
        with self.assertRaises(ValueError):
            bs.default_stages(supplies=["mic"])

    def test_default_stages_are_ten_in_beat_order(self):
        stages = bs.default_stages()
        self.assertEqual(len(stages), 10)
        self.assertEqual([s["id"] for s in stages], [
            "identity", "mic", "voice", "vad", "turn_taking",
            "secrets", "scheduler", "network", "calendar", "tutor"])
        self.assertEqual(stages[0]["beat"], "rings")
        self.assertTrue(all(s["beat"] == "suit"
                            for s in stages[1:]))
        self.assertEqual(bs.BEATS, ("bar", "rings", "suit", "reactor"))

    def test_log_lines_accumulate_in_order(self):
        seq = bs.BootSequence(
            stages=_fake_stages([("ready", ""), ("degraded", "down")]),
            clock=_fixed_clock(9))
        seq.run_all()
        log = seq.report()["log"]
        self.assertTrue(log[0].startswith("INITIATING SYSTEM — VYUK"))
        self.assertEqual(log[1], "[stage-0] ready")
        self.assertEqual(log[2], "[stage-1] degraded — down")


class TestDefaultStageChecks(unittest.TestCase):
    """The default checks, driven by supplied statuses and by
    the real (absent) engines on this lab machine — no real mic,
    no real audio, no engine import forced."""

    def test_mic_stage_with_supplied_status(self):
        seq = bs.BootSequence(
            stages=[{"id": "mic", "label": "Microphone",
                     "beat": "suit",
                     "check": lambda: bs._check_mic(
                         {"state": "ok", "device": "USB mic"})}], 
            clock=_fixed_clock(9))
        out = seq.advance()
        self.assertEqual(out["verdict"], "ready")
        self.assertEqual(out["detail"]["device"], "USB mic")

    def test_mic_stage_silent_is_degraded(self):
        out = bs._check_mic({"state": "silent", "device": None})
        self.assertEqual(out["verdict"], "degraded")
        self.assertIn("silent", out["reason"])

    def test_mic_stage_without_supplied_status_is_unchecked(self):
        out = bs._check_mic(None)
        self.assertEqual(out["verdict"], "degraded")
        self.assertFalse(out["checked"])
        self.assertIn("no mic session", out["reason"])

    def test_network_stage_with_supplied_probes(self):
        out = bs._check_network(["ok", "ok"])
        self.assertEqual(out["verdict"], "ready")
        out = bs._check_network(["ok", "weak"])
        self.assertEqual(out["verdict"], "degraded")
        self.assertIn("weak", out["reason"])

    def test_network_stage_without_probes_is_unchecked(self):
        out = bs._check_network(None)
        self.assertEqual(out["verdict"], "degraded")
        self.assertFalse(out["checked"])
        self.assertIn("no connectivity probe", out["reason"])

    def test_calendar_stage_with_supplied_status(self):
        out = bs._check_calendar({"connected": True})
        self.assertEqual(out["verdict"], "ready")
        out = bs._check_calendar({"connected": False,
                                  "reason": "not signed in"})
        self.assertEqual(out["verdict"], "degraded")
        self.assertIn("not signed in", out["reason"])

    def test_calendar_stage_without_supplied_status_is_unchecked(self):
        out = bs._check_calendar(None)
        self.assertEqual(out["verdict"], "degraded")
        self.assertFalse(out["checked"])

    def test_identity_stage_reads_config_and_table(self):
        # The live tree's config: en-gb / bm_george — both resolve.
        # (The check returns a flat verdict dict; the "detail"
        # key exists only on the staged report, not the raw check.)
        out = bs._check_identity()
        self.assertEqual(out["verdict"], "ready")
        self.assertEqual(out["language"], "en-gb")
        self.assertEqual(out["voice"], "bm_george")

    def test_voice_stage_reports_engine_unavailable_honestly(self):
        # On this lab machine kokoro-onnx is absent from both
        # interpreters: the stage degrades, never claims ready,
        # never raises.
        out = bs._check_voice()
        self.assertEqual(out["verdict"], "degraded")
        self.assertTrue(out["checked"])

    def test_vad_and_turn_taking_stages_report_fallbacks_honestly(self):
        # No weights on this lab machine: both degrade to their
        # labelled fallbacks, never ready, never a crash.
        for check in (bs._check_vad, bs._check_turn_taking):
            out = check()
            self.assertEqual(out["verdict"], "degraded")
            self.assertTrue(out["checked"])
            self.assertTrue(out["reason"])

    def test_secrets_and_scheduler_stages_report_honestly(self):
        for check in (bs._check_secrets, bs._check_scheduler):
            out = check()
            self.assertIn(out["verdict"], ("ready", "degraded"))
            self.assertTrue(out["checked"])

    def test_tutor_stage_reads_the_real_store(self):
        # The real store (memory_store) is injected — a bare
        # StudentModel() would read an empty in-memory store.
        out = bs._check_tutor()
        self.assertIn(out["verdict"], ("ready", "failed"))
        self.assertTrue(out["checked"])


if __name__ == "__main__":
    unittest.main()
