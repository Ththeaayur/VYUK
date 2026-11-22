"""GAPS-25 suite: flaws V-1..V-8 — voice pipeline (§6.5).

What this pins, per flaw:
- V-1: the shared loop passes the trailing silence that ended
  each utterance to on_utterance, and the barge-in discipline
  is the shared one (single chunk held, sustained cuts once).
- V-2: every genuine turn consults the turn detector (one
  detector per session, verdict recorded) and the verdict never
  gates dispatch — a turn whose verdict is "hold" is still
  dispatched.
- V-3: the boot ready-state string matches (smartturn-onnx).
- V-4: weights are absent (OWED, not built) — no .onnx in the
  tree, both finders return None, status surfaces agree
  degraded. Nothing here downloads anything.
- V-5: one VAD truth — both resolvers agree on the default
  config and on garbage, and the config defaults are pinned.
- V-6: one STT factory — exactly four WhisperModel
  constructions, live paths through the factory, clean
  degradation without faster-whisper.
- V-7: one live turn home + the noise-floor snapshot resets
  with each new estimator.
- V-8: the bridge lives in core/voice/ (shim re-exports
  identical objects, no old-path imports survive) and the gate
  is unchanged (OFF by default, Gemini refused).

Refactor batch: no tool is added. The census pins (755 tools /
382 read / 373 effectful / 164 confirm-gated, 2077 params,
752 grouped, 754 closed) are checked against the audit record
and the live registry.
"""

import asyncio
import pathlib
import queue
import re
import types
import unittest

import numpy as np

from core import provider_identity as pid
from core.config import config
from core.voice import mic_loop
from core.voice.asr import (
    _SHARED_MODELS,
    faster_whisper_available,
    get_whisper_model,
    release_whisper_model,
)
from core.voice.local_client_vad import (
    NoiseFloorEstimator,
    VAD_MIN_SPEECH_S,
    VAD_TRAILING_SILENCE_S,
    _barge_in_sustain_s,
    _noise_floor_snapshot,
    _vad_threshold,
)
from core.voice.smartturn import (
    consult_turn_detector,
    session_turn_detector,
)
from core.voice.vad import _energy_threshold

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHUNK_S = config.chunk_size / config.sample_rate_in
N_SPEECH = int(-(-VAD_MIN_SPEECH_S // CHUNK_S))
N_SILENCE = int(-(-VAD_TRAILING_SILENCE_S // CHUNK_S))
N_SUSTAIN = int(-(-_barge_in_sustain_s() // CHUNK_S))


def _pcm(rms_value, n_samples=1024):
    return np.full(n_samples, int(rms_value), dtype=np.int16).tobytes()


LOUD = _pcm(1000)
QUIET = _pcm(100)


class _FakeMic:
    def __init__(self, chunks):
        self.mic_queue = queue.Queue()
        for c in chunks:
            self.mic_queue.put(c)


def _drive(chunks, on_utterance, speaking=False, estimator=None):
    """Drive the shared loop once; return (interrupts, utterances)."""
    state = {"speaking": speaking, "interrupts": 0, "stop": False}

    async def _run():
        task = asyncio.create_task(mic_loop.run_mic_loop(
            _FakeMic(chunks),
            running=lambda: not state["stop"],
            is_speaking=lambda: state["speaking"],
            set_speaking=lambda v: state.__setitem__("speaking", v),
            interrupt_playback=lambda: state.__setitem__(
                "interrupts", state["interrupts"] + 1),
            on_utterance=on_utterance,
            estimator=estimator or NoiseFloorEstimator()))
        await asyncio.sleep(0.5)
        state["stop"] = True
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(_run())
    return state["interrupts"]


def _code_text(text):
    text = re.sub(r'"""[\s\S]*?"""', '""', text)
    text = re.sub(r"'''[\s\S]*?'''", "''", text)
    return "\n".join(ln.split("#", 1)[0] for ln in text.splitlines())


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


class SharedLoopSilenceTests(unittest.TestCase):
    def test_utterance_carries_its_trailing_silence(self):
        seen = []

        async def _collect(pcm, trailing_silence_s=0.0):
            seen.append((pcm, trailing_silence_s))

        _drive([LOUD] * N_SPEECH + [QUIET] * N_SILENCE, _collect)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0][0], LOUD * N_SPEECH)
        # The silence that ended the turn: at least the endpoint
        # rule, less than one chunk more.
        self.assertGreaterEqual(seen[0][1], VAD_TRAILING_SILENCE_S)
        self.assertLess(seen[0][1],
                        VAD_TRAILING_SILENCE_S + CHUNK_S)

    def test_barge_in_discipline_is_the_shared_one(self):
        utterances = []

        async def _collect(pcm, trailing_silence_s=0.0):
            utterances.append(pcm)

        held = _drive([LOUD] + [QUIET] * 20, _collect, speaking=True)
        self.assertEqual(held, 0)
        self.assertEqual(utterances, [])
        cut = _drive([LOUD] * N_SUSTAIN + [LOUD] * N_SPEECH
                     + [QUIET] * N_SILENCE, _collect, speaking=True)
        self.assertEqual(cut, 1)
        self.assertEqual(len(utterances), 1)


class ConsultTests(unittest.TestCase):
    def test_consult_records_a_verdict_on_the_session(self):
        session = types.SimpleNamespace()
        verdict = consult_turn_detector(
            session, LOUD * 10, "hello world", N_SILENCE * CHUNK_S)
        self.assertIsInstance(verdict, dict)
        self.assertEqual(verdict["decision"], "hold")
        self.assertEqual(verdict["backend"], "heuristic-end-of-turn")
        self.assertIs(session._last_turn_verdict, verdict)

    def test_one_detector_per_session(self):
        session = types.SimpleNamespace()
        first = session_turn_detector(session)
        second = session_turn_detector(session)
        self.assertIs(first, second)
        other = session_turn_detector(types.SimpleNamespace())
        self.assertIsNot(first, other)

    def test_consult_never_raises(self):
        class _Broken:
            def decide(self, *a, **k):
                raise RuntimeError("neural backend exploded")

        session = types.SimpleNamespace(_turn_detector=_Broken())
        verdict = consult_turn_detector(
            session, LOUD * 10, "hello world", 0.8)
        self.assertEqual(verdict["decision"], "unknown")
        self.assertIn("failed", verdict["reason"])
        self.assertIs(session._last_turn_verdict, verdict)

    def test_all_three_handlers_consult(self):
        for rel in ("core/voice/local_client_mic.py",
                    "core/voice/cloud_text_lifecycle.py",
                    "core/voice/lite_client_session.py"):
            with self.subTest(engine=rel):
                self.assertIn("consult_turn_detector(",
                              _code_text(_read(rel)))


class VerdictNeverGatesTests(unittest.TestCase):
    def test_turn_dispatched_with_a_hold_verdict(self):
        utterances = []
        verdicts = []
        session = types.SimpleNamespace()

        async def _collect(pcm, trailing_silence_s=0.0):
            verdicts.append(consult_turn_detector(
                session, pcm, "hello world", trailing_silence_s))
            utterances.append(pcm)

        _drive([LOUD] * N_SPEECH + [QUIET] * N_SILENCE, _collect)
        self.assertEqual(len(utterances), 1)
        self.assertEqual(len(verdicts), 1)
        # The facade holds at 0.8 s of trailing silence — and the
        # turn was dispatched anyway. The verdict observes; it
        # never gates.
        self.assertEqual(verdicts[0]["decision"], "hold")

    def test_no_handler_branches_on_the_verdict(self):
        for rel in ("core/voice/local_client_mic.py",
                    "core/voice/cloud_text_lifecycle.py",
                    "core/voice/lite_client_session.py"):
            with self.subTest(engine=rel):
                for line in _code_text(_read(rel)).splitlines():
                    self.assertIsNone(
                        re.search(r"if .*_last_turn_verdict", line),
                        f"{rel}: {line.strip()}")


class WeightsOwedTests(unittest.TestCase):
    def test_no_onnx_weights_in_the_tree(self):
        onnx = [str(p.relative_to(ROOT))
                for p in ROOT.rglob("*.onnx")]
        self.assertEqual(onnx, [])

    def test_weight_finders_return_none(self):
        from core.voice.smartturn import find_smartturn_weights
        from core.voice.vad import find_silero_weights
        self.assertIsNone(find_smartturn_weights())
        self.assertIsNone(find_silero_weights())

    def test_status_surfaces_agree_degraded(self):
        from core.desktop import turn_taking as tt
        from core.desktop import voice_activity as va
        vad = va.engine_status()
        st = tt.engine_status()
        self.assertEqual(vad.get("backend"), "energy")
        self.assertEqual(st.get("backend"), "heuristic-end-of-turn")
        self.assertIs(vad.get("degraded"), True)
        self.assertIs(st.get("degraded"), True)


class VadTruthTests(unittest.TestCase):
    def test_resolvers_agree_on_default_and_garbage(self):
        self.assertEqual((_energy_threshold(), _vad_threshold()),
                         (400.0, 400.0))
        orig = config.vad_rms_threshold
        try:
            config.vad_rms_threshold = -5
            self.assertEqual((_energy_threshold(), _vad_threshold()),
                             (400.0, 400.0))
        finally:
            config.vad_rms_threshold = orig

    def test_config_defaults_pinned(self):
        self.assertEqual(config.vad_rms_threshold, 400)
        self.assertEqual(config.barge_in_factor, 1.6)
        self.assertEqual(config.barge_in_guard_ms, 250)
        self.assertEqual(config.barge_in_sustain_ms, 150)


class SttFactoryTests(unittest.TestCase):
    def test_exactly_four_whisper_constructions(self):
        sites = []
        for rel in ("core", "gui"):
            for path in sorted((ROOT / rel).rglob("*.py")):
                for i, line in enumerate(
                        _code_text(path.read_text(
                            encoding="utf-8")).splitlines(), 1):
                    if "WhisperModel(" in line:
                        sites.append(f"{path.relative_to(ROOT)}:{i}")
        self.assertEqual(len(sites), 4)
        self.assertTrue(any("core/voice/asr.py" in s for s in sites))
        self.assertTrue(any("local_assets_whisper.py" in s
                            for s in sites))
        self.assertTrue(any("skills_senses_checks.py" in s
                            for s in sites))
        self.assertTrue(any("audio_localise.py" in s for s in sites))

    def test_live_paths_use_the_factory(self):
        for rel in ("core/voice/local_client_lifecycle.py",
                    "core/voice/lite_client_session.py",
                    "core/voice/asr.py"):
            with self.subTest(path=rel):
                self.assertIn("get_whisper_model(",
                              _code_text(_read(rel)))

    def test_factory_degrades_without_faster_whisper(self):
        self.assertFalse(faster_whisper_available())
        self.assertIsNone(get_whisper_model("tiny"))
        self.assertEqual(_SHARED_MODELS, {})
        # Releasing a model that was never cached never raises.
        release_whisper_model(object())
        self.assertEqual(_SHARED_MODELS, {})


class NoiseFloorSnapshotTests(unittest.TestCase):
    def test_new_estimator_resets_the_snapshot(self):
        est1 = NoiseFloorEstimator()
        est1.observe(300.0, in_utterance=False)
        self.assertIsNotNone(_noise_floor_snapshot())
        NoiseFloorEstimator()
        self.assertIsNone(_noise_floor_snapshot())

    def test_snapshot_republishes_on_observe(self):
        est = NoiseFloorEstimator()
        self.assertIsNone(_noise_floor_snapshot())
        est.observe(500.0, in_utterance=False)
        snap = _noise_floor_snapshot()
        self.assertIsNotNone(snap)
        self.assertEqual(snap["samples"], 1)

    def test_no_production_construction_of_turn_state_machines(self):
        for rel in ("core", "gui"):
            for path in sorted((ROOT / rel).rglob("*.py")):
                if path.name in ("turn_taking.py", "voice_activity.py"):
                    continue  # their own lazy _DEFAULT singletons
                text = _code_text(path.read_text(encoding="utf-8"))
                self.assertNotIn("TurnPredictor(", text,
                                 f"{path.relative_to(ROOT)}")
                self.assertNotIn("SpeechDetector(", text,
                                 f"{path.relative_to(ROOT)}")


class BridgeMoveTests(unittest.TestCase):
    def test_canonical_module_exists_and_shim_matches(self):
        # The shim itself is NEVER imported here: the Phase-6
        # guard (test_refactor_phase6_guards) forbids constructed
        # imports of old paths anywhere in the tree, and a static
        # old-path import is a flat import of a packaged stem —
        # both trip the guard. So this test proves the shim by
        # its text (a re-export of the canonical module, nothing
        # else) and proves the wiring by the facade, which must
        # resolve to the canonical module. The object-identity
        # proof (shim re-exports identical objects) lives in
        # prove_gaps25.py, which runs outside the tree and is
        # not subject to the guard.
        from core.voice import voice_text_bridge as new
        shim = _read("core/voice_text_bridge.py")
        self.assertIn("from .voice.voice_text_bridge import *", shim)
        self.assertIn("from .voice.voice_text_bridge import __all__",
                      shim)
        self.assertIn("attach_text_brain_candidate", new.__all__)
        self.assertIn("VOICE_FREEFORM_COMPANION_GATE", new.__all__)
        from core.gui_facade import voice_text_bridge as via_facade
        self.assertIs(via_facade, new)

    def test_no_old_path_imports_survive(self):
        for rel in ("core", "gui", "tests"):
            for path in sorted((ROOT / rel).rglob("*.py")):
                if str(path.relative_to(ROOT)) == \
                        "core/voice_text_bridge.py":
                    continue  # the shim itself
                for i, line in enumerate(
                        path.read_text(encoding="utf-8").splitlines(), 1):
                    if line.strip().startswith("#"):
                        continue
                    self.assertIsNone(
                        re.search(
                            r"^\s*from (core|\.|\.\.) import "
                            r"voice_text_bridge\b", line),
                        f"{path.relative_to(ROOT)}:{i}: {line.strip()}")
                    self.assertIsNone(
                        re.search(
                            r"^\s*from core\.voice_text_bridge import\b",
                            line),
                        f"{path.relative_to(ROOT)}:{i}: {line.strip()}")

    def test_production_importers_use_the_new_path(self):
        self.assertIn("from .voice import voice_text_bridge",
                      _read("core/brain_capabilities.py"))
        self.assertIn("from ..voice import voice_text_bridge",
                      _read("core/brain/voice_goal.py"))
        self.assertIn('".voice.voice_text_bridge"',
                      _read("core/gui_facade.py"))

    def test_bridge_still_gated(self):
        from core.voice import voice_text_bridge as bridge
        self.assertFalse(bridge.is_voice_bridge_enabled())
        self.assertIn("GATED, not built",
                      bridge.VOICE_FREEFORM_COMPANION_GATE)
        gemini = types.SimpleNamespace(engine=pid.Engine.GEMINI,
                                       run_goal=lambda t: "x")
        self.assertFalse(bridge.attach_text_brain_candidate(gemini))
        self.assertFalse(bridge.attach_text_brain_candidate(None))
        self.assertIsNone(bridge.get_text_brain())

        async def _hand_off():
            return await bridge.hand_off(
                types.SimpleNamespace(on_transcript=lambda *a: None),
                "do a thing", text_brain=gemini)

        self.assertIsNone(asyncio.run(_hand_off()))


class BootStringTests(unittest.TestCase):
    def test_boot_expects_smartturn_onnx(self):
        boot = _read("core/desktop/boot_sequence.py")
        self.assertIn('"smartturn-onnx"', boot)
        self.assertNotIn('"smartturn-neural"', boot)
        for rel in ("core",):
            for path in sorted((ROOT / rel).rglob("*.py")):
                self.assertNotIn(
                    "smartturn-neural",
                    path.read_text(encoding="utf-8"),
                    f"{path.relative_to(ROOT)}")


class CensusTests(unittest.TestCase):
    def test_audit_totals_unchanged(self):
        m = re.search(
            r'AUDIT-TOTALS: \{"total": (\d+), "read": (\d+), '
            r'"effectful": (\d+), "confirm_gated": (\d+), '
            r'"seam_wired": (\d+), "already_verified_in_handler": '
            r'(\d+), "honestly_unverifiable": (\d+), '
            r'"next_pass_pool": (\d+)\}',
            _read("TOOLS_LOOPHOLES_AUDIT.md"))
        self.assertIsNotNone(m)
        self.assertEqual(tuple(int(g) for g in m.groups()),
                         (764, 384, 380, 169, 44, 314, 22, 0))  # INTENTIONAL — field-check fix #32 (2026-11-18): 755/373/307 -> 756/374/308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756/374/164/308 -> 757/375/165/309 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169

    def test_live_registry_unchanged(self):
        from core.catalogue_groups_data import GROUP_MEMBERS
        from core.funnel.tools_gate import _CONFIRM_GATE_TOOLS
        from core.funnel.tools_registry import tools as registry
        tools = registry._tools
        grouped = set()
        for members in GROUP_MEMBERS.values():
            grouped.update(members)
        params = sum(len((s.get("parameters") or {}).get("properties")
                         or {}) for s in tools.values())
        self.assertEqual(len(tools), 764)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 758 -> 762 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(params, 2102)  #Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (Group E, 2026-11-22): 2079 -> 2095 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(
            len([n for n in tools if n in _CONFIRM_GATE_TOOLS]), 169)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan (confirm-gated); fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 section F fix F-F303 (2026-11-21): 166 -> 167 — notify_user classified confirm-gated; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        self.assertEqual(len(grouped), 761)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing; Stage 0 features batch 2 (Group E, 2026-11-22): 755 -> 759 — find_free_slots, snooze_email, cancel_calendar_event, reschedule_calendar_event added; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169


if __name__ == "__main__":
    unittest.main()
