"""GAPS-22: the section 6.2 B-series remainder (brain/engine selection).

B-V1 — one model resolution: local_assets._existing_tier_file and
bundled_model_path go through model_manager.resolve_model, with the
MIN_MODEL_BYTES readiness floor (a partial download never satisfies
a tier, preferred file included).
B-V2 — one tier table: core/model_tiers.py; model_manager.TIER_FILES
and local_assets.MODEL_TIERS are projections/imports of it, never
hand copies.
B-V3 — one engine identity: provider_identity.Engine (a str enum —
str() and == "gemini" still behave); the four sessions declare
their engine; no class-name checks remain.
B-V4 — one engine interface: core/brain/engine_session.EngineSession
+ check_session_conforms(); _bind refuses a non-conforming inner.
B-V5 — one ownership story (proxy owns _inner except the borrowed
Gemini singleton) + connected_model pinned at connect; the
summariser and the cost record name the connected model.
B-V6 — standby release goes through the session's own
release_model(), never an attribute-name loop.
B-V7 — the frozen-brain check fails CLOSED (unconsultable kill
chain reads as frozen; local serves).
B-V8 — the turn profile starts UNDECIDED (None); the one named
entry profile (ENTRY_PROFILE) is read through entry_profile().
B-V10 — engine health: core/brain/engine_health.can_serve(); a
cloud engine that cannot serve is discovered at start() before
any connect attempt.
B-V11 — the routing loop: record_turn_outcome() + the loop rules
(failure step-up, cap step-down, cap wins) + the mic wiring
(hints + ledger snapshot in, outcome out).
B-V9 — already closed by GAPS-6; pinned here by the gap proof,
not rebuilt.
"""

import asyncio
import types
import unittest

from core import model_manager as mm
from core import model_tiers as mt
from core import provider_identity as pi
from core.provider_identity import Engine
from core.brain import engine_health as eh
from core.brain import engine_session as es
from core.brain.brain_session_core import BrainSessionCoreMixin
from core import query_routing as qr
from core.voice import local_assets as la
from core.voice import local_assets_model as lam
from core.voice.cloud_text_client import GroqSession
from core.voice.lite_client_session import LiteLiveSession
from core.voice.live_client import GeminiLiveSession, live_session
from core.voice.local_client import LocalLiveSession
from core.voice.local_client_classifier import ENTRY_PROFILE, entry_profile

ROUTINE = ("Summarise the budget review and list the three main "
           "changes since last quarter")
HARD = ("Explain step by step how to solve this integral equation "
        "with a matrix, show your working in detail, and compute "
        "the derivative as well as the probability.")
ALL = {"lite": True, "balanced": True, "quality": True}


def _fake_inner():
    async def _start(_audio_manager):
        return None
    return types.SimpleNamespace(
        engine=Engine.LOCAL, connected_model=None, is_connected=False,
        start=_start, stop=lambda: None, release_model=lambda: None)


def _run_start(engine_request, *, brain_frozen, health=None):
    """The real BrainSessionCoreMixin.start() with a fake inner.

    ``brain_frozen`` is the kill-chain answer (or an exception to
    raise); ``health`` replaces can_serve_configured's answer.
    Restores config + kill chain + health afterwards.
    """
    from core import config as cfg_mod
    from core.funnel import kill_chain as kc
    import core.brain.brain_session_core as bsc
    mixin = BrainSessionCoreMixin()
    saved = (cfg_mod.config.brain_engine, kc.brain_frozen,
             bsc.can_serve_configured)
    try:
        cfg_mod.config.brain_engine = engine_request
        if isinstance(brain_frozen, Exception):
            def _raise():
                raise brain_frozen
            kc.brain_frozen = _raise
        else:
            kc.brain_frozen = lambda: brain_frozen
        if health is not None:
            bsc.can_serve_configured = lambda _e: health
        mixin._build_local_inner = lambda: _fake_inner()
        mixin._start_companion = lambda: None

        async def _noop_cloud(_inner, _audio_manager):
            return None
        mixin._start_cloud = _noop_cloud
        asyncio.run(mixin.start(None))
        return mixin
    finally:
        cfg_mod.config.brain_engine, kc.brain_frozen, \
            bsc.can_serve_configured = saved


class TierTableTests(unittest.TestCase):
    def test_one_table_identity(self):
        self.assertIs(lam.MODEL_TIERS, mt.MODEL_TIERS)
        self.assertIs(la.MODEL_TIERS, mt.MODEL_TIERS)
        self.assertEqual(
            mm.TIER_FILES,
            {t["file"]: n for n, t in mt.MODEL_TIERS.items()})
        self.assertEqual(mm.TIER_ORDER, mt.TIER_ORDER)

    def test_resolve_model_floor(self):
        disc = {"models": [
            {"id": "Qwen3-8B-Instruct-2507-Q4_K_M.gguf", "kind": "brain",
             "tier": "quality", "size_bytes": 500},
            {"id": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "kind": "brain",
             "tier": "balanced", "size_bytes": 2_600_000_000}]}
        got = mm.resolve_model(disc, min_bytes=lam.MIN_MODEL_BYTES)
        self.assertEqual((got["status"], got["tier"]), ("ok", "balanced"))
        got = mm.resolve_model(
            disc, preferred_file="Qwen3-8B-Instruct-2507-Q4_K_M.gguf",
            min_bytes=lam.MIN_MODEL_BYTES)
        self.assertEqual((got["status"], got["tier"]), ("ok", "balanced"))
        got = mm.resolve_model(disc, min_bytes=0)
        self.assertEqual((got["status"], got["tier"]), ("ok", "quality"))


class EngineIdentityTests(unittest.TestCase):
    def test_engine_is_a_str_enum(self):
        self.assertTrue(issubclass(Engine, str))
        self.assertEqual(str(Engine.GEMINI), "gemini")
        self.assertEqual(f"{Engine.LOCAL}", "local")
        self.assertEqual(Engine.GROQ, "groq")

    def test_resolver_returns_engine_members(self):
        self.assertIs(
            pi.resolve_provider("local", "gemini")["provider"],
            Engine.LOCAL)
        self.assertIs(
            pi.resolve_provider("x", "groq")["provider"], Engine.GROQ)

    def test_sessions_declare_engine(self):
        self.assertIs(live_session.engine, Engine.GEMINI)
        self.assertIs(GroqSession.engine, Engine.GROQ)
        self.assertIs(LocalLiveSession.engine, Engine.LOCAL)
        self.assertIs(LiteLiveSession.engine, Engine.LOCAL)
        self.assertIs(LiteLiveSession.is_lite, True)


class SessionInterfaceTests(unittest.TestCase):
    def test_all_four_conform(self):
        for session in (live_session, GroqSession(),
                        LocalLiveSession(), LiteLiveSession()):
            es.check_session_conforms(session)
            self.assertIsInstance(session, es.EngineSession)

    def test_non_conforming_inner_names_the_member(self):
        with self.assertRaises(TypeError) as ctx:
            es.check_session_conforms(types.SimpleNamespace(
                engine=Engine.LOCAL, connected_model=None))
        self.assertIn("is_connected", str(ctx.exception))

    def test_bind_refuses_non_conforming_inner(self):
        mixin = BrainSessionCoreMixin()
        with self.assertRaises(TypeError):
            mixin._bind(types.SimpleNamespace(engine=None))

    def test_release_model_never_raises_and_drops(self):
        local = LocalLiveSession()
        local._llama = types.SimpleNamespace()
        local._whisper = types.SimpleNamespace()
        local._llama_unloaded = False
        local.release_model()
        self.assertIsNone(local._llama)
        self.assertIsNone(local._whisper)
        self.assertTrue(local._llama_unloaded)
        lite = LiteLiveSession()
        lite._whisper = types.SimpleNamespace()
        lite.release_model()
        self.assertIsNone(lite._whisper)
        self.assertIsNone(lite._inner)
        live_session.release_model()
        GroqSession().release_model()


class FrozenTests(unittest.TestCase):
    def test_unconsultable_kill_chain_reads_as_frozen(self):
        mixin = _run_start("gemini",
                           brain_frozen=RuntimeError("state unreadable"))
        self.assertIs(mixin._engine, Engine.LOCAL)
        self.assertTrue(mixin._frozen_override)
        self.assertIn("frozen", mixin._fallback_reason)

    def test_frozen_true_forces_local_unfrozen_keeps_request(self):
        mixin = _run_start("gemini", brain_frozen=True)
        self.assertIs(mixin._engine, Engine.LOCAL)
        self.assertTrue(mixin._frozen_override)
        mixin = _run_start(
            "gemini", brain_frozen=False,
            health={"ok": True, "reason": "key present",
                    "engine": Engine.GEMINI, "serves": "model",
                    "checked": ["gemini key present"]})
        self.assertIs(mixin._engine, Engine.GEMINI)


class ProfileTests(unittest.TestCase):
    def test_fresh_session_profile_is_undecided(self):
        self.assertIsNone(LocalLiveSession()._active_profile)

    def test_entry_profile(self):
        self.assertEqual(ENTRY_PROFILE, "quick")
        self.assertEqual(entry_profile(LocalLiveSession()), "quick")
        self.assertEqual(
            entry_profile(types.SimpleNamespace(_active_profile="think")),
            "think")


class HealthTests(unittest.TestCase):
    def test_can_serve_matrix(self):
        self.assertEqual(
            eh.can_serve("local", local_model_ready=False)["serves"],
            "lite")
        self.assertEqual(
            eh.can_serve("local", local_model_ready=True)["serves"],
            "model")
        self.assertFalse(
            eh.can_serve("gemini", gemini_key_present=False)["ok"])
        self.assertTrue(
            eh.can_serve("gemini", gemini_key_present=True)["ok"])
        self.assertFalse(eh.can_serve("bogus")["ok"])

    def test_unservable_cloud_goes_straight_to_local(self):
        mixin = _run_start(
            "gemini", brain_frozen=False,
            health={"ok": False, "reason": "TEST: no API key",
                    "engine": Engine.GEMINI, "serves": None,
                    "checked": ["gemini key present"]})
        self.assertIs(mixin._engine, Engine.LOCAL)
        self.assertEqual(mixin._fallback_reason, "TEST: no API key")
        self.assertFalse(mixin._frozen_override)


class RoutingLoopTests(unittest.TestCase):
    def setUp(self):
        qr._outcomes.clear()
        qr._history.clear()

    def tearDown(self):
        qr._outcomes.clear()
        qr._history.clear()

    def test_outcome_ring(self):
        qr.record_turn_outcome({"tier": "balanced"}, ok=True,
                               repairs=1, tool_calls=2)
        outcome = qr.last_turn_outcome()
        self.assertEqual((outcome["tier"], outcome["ok"]), ("balanced", True))
        for _ in range(25):
            qr.record_turn_outcome({"tier": "lite"}, ok=True)
        self.assertEqual(len(qr.turn_outcomes()), 20)

    def test_failure_step_up(self):
        for _ in range(3):
            qr.record_turn_outcome({"tier": "balanced"}, ok=False)
        decision = qr.route_query(ROUTINE, availability=dict(ALL),
                                  effective_tier="balanced")
        self.assertEqual(decision["tier"], "quality")
        self.assertEqual(decision["loop"]["rule"], "failure-step-up")

    def test_cap_step_down(self):
        decision = qr.route_query(
            HARD, availability=dict(ALL), effective_tier="balanced",
            ledger={"spend_today_usd": 5.0, "daily_cap_usd": 5.0,
                    "degraded": True})
        self.assertEqual(decision["tier"], "balanced")
        self.assertEqual(decision["loop"]["rule"], "cap-step-down")
        decision = qr.route_query(HARD, availability=dict(ALL),
                                  effective_tier="balanced", ledger=None)
        self.assertEqual(decision["tier"], "quality")
        self.assertIsNone(decision["loop"])

    def test_cap_wins_when_both_fire(self):
        for _ in range(3):
            qr.record_turn_outcome({"tier": "quality"}, ok=False)
        decision = qr.route_query(
            HARD, availability=dict(ALL), effective_tier="balanced",
            ledger={"spend_today_usd": 5.0, "daily_cap_usd": 5.0,
                    "degraded": True})
        self.assertEqual(decision["tier"], "balanced")
        self.assertEqual(decision["loop"]["rule"], "cap-step-down")


if __name__ == "__main__":
    unittest.main()
