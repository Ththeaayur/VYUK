"""Phase 1 (fourth audit C03/C05/C08/C09 + fifth audit F04).

One theme — state visibility: the app must be able to tell the
user what brain is running, what is degraded, and whether a typed
message was accepted or dropped.

C09: provider identity is one explicit resolver
(core/provider_identity), not three ad-hoc normalizations; dispatch
is unchanged, but an unrecognized value resolving to gemini is now
flagged instead of silent, and the resolver is pinned to agree with
config.selected_provider() (Phase 0, C01) across the input matrix.

C03: BrainSession records requested-vs-effective provider and the
fallback reason at start()/fallback time (provider_status()), and
the frozen-brain override is recorded instead of being visible
only as an engine display reading "local".

C08: BrainSession.readiness() reports connection, model, and voice
as separate layers instead of one is_connected boolean.

C05: the local session exposes voice_ready (whisper actually
loaded), distinct from is_connected — the signal _prepare_builtin
used to discard.

F04: typed sends return a delivery result (accepted / dropped /
ignored) at every brain layer instead of a silent no-op, and the
GUI's typed-send done callback surfaces a dropped result with
retry guidance. The GUI's submit-time not-connected branch already
rendered its own message + draft restore — that branch is
pin-verified untouched, not duplicated.

Fault injection: the cloud→local fallback is driven with fake
inners and injected failure reasons (ERROR text, MISSING_API_KEY,
empty reason, stop-requested gate, both-brains-fail chain).

LAB NOTE: GUI modules import the frozen ``voice_assistant.*``
package names and cannot be imported in this lab (the same
limitation the UI suites document). GUI behaviour here is
therefore verified by AST over the real files, following that
precedent — it proves the wiring is present, NOT rendered
appearance; field proof on real Windows is still owed.
"""

import asyncio
import ast
import unittest
from pathlib import Path

from core import provider_identity
from core.brain.brain import BrainSession  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: brain hub moved to core/brain/brain.py
from core.voice.cloud_text_client import GroqSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.config import AppConfig
from core.voice.lite_client import LiteLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.voice.local_client import LocalLiveSession  # Phase 3 V1b INTENTIONAL: moved to core/voice/
from core.provider_identity import Engine, resolve_provider

TREE = Path(__file__).resolve().parent.parent
GUI = TREE / "gui"


def _gui_source(name):
    return (GUI / name).read_text(encoding="utf-8")


class _FakeInner:
    """Minimal inner session: settable is_connected + send result."""

    def __init__(self, connected=True, send_result=None):
        self.is_connected = connected
        self._send_result = send_result
        self.started = False
        self.statuses = []
        # GAPS-22 (B-V4): the interface _bind enforces — a fake
        # inner that goes through the fallback path must conform
        # like a real session. This fake stands in for a LOCAL
        # session (engine LOCAL, no model connected).
        self.engine = Engine.LOCAL
        self.connected_model = None

    def stop(self):
        self.is_connected = False

    def release_model(self):
        return None

    async def send_text(self, text):
        return self._send_result

    async def start(self, audio_manager):
        self.started = True


class _FakeLocalInner(_FakeInner):
    """Fallback-target inner: emits statuses through the bound
    trampoline like a real session would."""

    def __init__(self, connected=True, error=None):
        super().__init__(connected=connected)
        self._error = error

    async def start(self, audio_manager):
        self.started = True
        if self._error is not None:
            self.on_status_change(self._error)
        self.is_connected = self.is_connected


class LiteLiveSessionFake:
    """Duck-typed fake for the readiness lite branch (the real
    branch keys on the lite-only ``_mic_started`` flag)."""

    def __init__(self, mic_started):
        self.is_connected = True
        self._mic_started = mic_started


# ---------------------------------------------------------------- C09

class TestProviderIdentity(unittest.TestCase):
    def test_local_engine_wins_over_cloud_value(self):
        for cloud in ("groq", "gemini", "bogus", "", None):
            res = resolve_provider("local", cloud)
            self.assertEqual(res["provider"], "local")
            self.assertFalse(res["normalized"])

    def test_groq_selected_only_by_exact_groq(self):
        self.assertEqual(
            resolve_provider("gemini", "groq")["provider"], "groq")
        self.assertFalse(
            resolve_provider("gemini", "groq")["normalized"])

    def test_unknown_cloud_value_still_serves_gemini_but_flagged(self):
        # Dispatch unchanged (gemini), normalization now VISIBLE.
        res = resolve_provider("gemini", "bogus-provider")
        self.assertEqual(res["provider"], "gemini")
        self.assertTrue(res["normalized"])
        self.assertEqual(res["raw_cloud_provider"], "bogus-provider")

    def test_case_and_whitespace_insensitive(self):
        res = resolve_provider("  GEMINI ", " GROQ ")
        self.assertEqual(res["provider"], "groq")
        self.assertFalse(res["normalized"])

    def test_empty_inputs_fall_back_to_flagged_gemini(self):
        res = resolve_provider("", "")
        self.assertEqual(res["provider"], "gemini")
        self.assertTrue(res["normalized"])

    def test_agrees_with_config_selected_provider(self):
        # The Phase 0 resolver and this one must never drift.
        for engine in ("local", "gemini", "bogus", "", None):
            for cloud in ("groq", "gemini", "bogus", "", None):
                cfg = AppConfig()
                cfg.brain_engine = engine
                cfg.cloud_provider = cloud
                self.assertEqual(
                    cfg.selected_provider(),
                    resolve_provider(engine, cloud)["provider"],
                    msg=f"engine={engine!r} cloud={cloud!r}")

    def test_provider_constants_cover_outputs(self):
        for engine in ("local", "gemini", "x"):
            for cloud in ("groq", "gemini", "x"):
                self.assertIn(
                    resolve_provider(engine, cloud)["provider"],
                    provider_identity.PROVIDERS)


# ---------------------------------------------------------------- C03

class TestProviderStatus(unittest.TestCase):
    def test_fresh_proxy_reports_no_request(self):
        status = BrainSession().provider_status()
        self.assertIsNone(status["requested"])
        self.assertIsNone(status["effective"])
        self.assertFalse(status["differs"])
        self.assertFalse(status["fallback_active"])
        self.assertIsNone(status["fallback_reason"])
        self.assertFalse(status["frozen_override"])

    def test_matching_request_and_effective(self):
        proxy = BrainSession()
        proxy._requested_provider = "gemini"
        proxy._engine = "gemini"
        status = proxy.provider_status()
        self.assertFalse(status["differs"])
        self.assertFalse(status["fallback_active"])

    def test_frozen_override_is_recorded_separately(self):
        proxy = BrainSession()
        proxy._requested_provider = "groq"
        proxy._engine = "local"
        proxy._frozen_override = True
        status = proxy.provider_status()
        self.assertTrue(status["differs"])
        self.assertTrue(status["frozen_override"])
        # Frozen is not a connect-failure fallback.
        self.assertFalse(status["fallback_active"])
        self.assertIsNone(status["fallback_reason"])


class TestFallbackFaultInjection(unittest.TestCase):
    def _proxy_with_cloud_failure(self, last_status):
        proxy = BrainSession()
        proxy._requested_provider = "gemini"
        proxy._engine = "gemini"
        proxy._inner = _FakeInner(connected=False)
        proxy._last_status = last_status
        proxy._saw_connected = False
        proxy._stop_requested = False
        emitted = []
        proxy.on_status_change = emitted.append
        return proxy, emitted

    def test_injected_error_reason_surfaces_in_status(self):
        proxy, emitted = self._proxy_with_cloud_failure(
            "ERROR: 401 bad API key")
        local = _FakeLocalInner(connected=True)
        asyncio.run(proxy._maybe_fallback_to_local(None, prepared=local))
        self.assertTrue(local.started)
        status = proxy.provider_status()
        self.assertEqual(status["requested"], "gemini")
        self.assertEqual(status["effective"], "local")
        self.assertTrue(status["fallback_active"])
        self.assertIn("401", status["fallback_reason"])
        self.assertTrue(any(s.startswith("FALLBACK:") for s in emitted))

    def test_missing_api_key_reason_preserved_verbatim(self):
        proxy, _ = self._proxy_with_cloud_failure("MISSING_API_KEY")
        asyncio.run(proxy._maybe_fallback_to_local(
            None, prepared=_FakeLocalInner(connected=True)))
        self.assertEqual(
            proxy.provider_status()["fallback_reason"], "MISSING_API_KEY")

    def test_empty_reason_gets_default_sentence(self):
        proxy, _ = self._proxy_with_cloud_failure("LISTENING")
        asyncio.run(proxy._maybe_fallback_to_local(
            None, prepared=_FakeLocalInner(connected=True)))
        self.assertEqual(
            proxy.provider_status()["fallback_reason"],
            "the cloud brain didn't connect")

    def test_stop_requested_suppresses_fallback(self):
        proxy, emitted = self._proxy_with_cloud_failure(
            "ERROR: 401 bad API key")
        proxy._stop_requested = True
        local = _FakeLocalInner(connected=True)
        asyncio.run(proxy._maybe_fallback_to_local(None, prepared=local))
        self.assertFalse(local.started)
        self.assertIsNone(proxy.provider_status()["fallback_reason"])
        self.assertEqual(proxy._engine, "gemini")
        self.assertFalse(any(s.startswith("FALLBACK:") for s in emitted))

    def test_connected_cloud_suppresses_fallback(self):
        proxy, _ = self._proxy_with_cloud_failure("ERROR: boom")
        proxy._saw_connected = True
        local = _FakeLocalInner(connected=True)
        asyncio.run(proxy._maybe_fallback_to_local(None, prepared=local))
        self.assertFalse(local.started)

    def test_both_brains_fail_names_both_reasons(self):
        proxy, emitted = self._proxy_with_cloud_failure(
            "ERROR: cloud unreachable")
        local = _FakeLocalInner(
            connected=False, error="ERROR: model file missing")
        asyncio.run(proxy._maybe_fallback_to_local(None, prepared=local))
        combined = [s for s in emitted if s.startswith("ERROR:")]
        self.assertTrue(combined)
        self.assertIn("cloud unreachable", combined[-1])
        self.assertIn("model file missing", combined[-1])


# ---------------------------------------------------------------- C08/C05

class TestReadiness(unittest.TestCase):
    def test_no_session_all_layers_down(self):
        ready = BrainSession().readiness()
        self.assertEqual(ready["provider"], "none")
        for layer in ("connection", "model", "voice"):
            self.assertFalse(ready[layer]["ok"])

    def test_gemini_live_all_layers_up(self):
        proxy = BrainSession()
        proxy._engine = "gemini"
        proxy._inner = _FakeInner(connected=True)
        ready = proxy.readiness()
        for layer in ("connection", "model", "voice"):
            self.assertTrue(ready[layer]["ok"], msg=layer)

    def test_local_voice_down_while_connected(self):
        # THE C05 split: connected must not imply voice-ready.
        proxy = BrainSession()
        proxy._engine = "local"
        inner = _FakeInner(connected=True)
        inner._backend = "builtin"
        inner._llama = object()
        inner.voice_ready = False
        proxy._inner = inner
        ready = proxy.readiness()
        self.assertTrue(ready["connection"]["ok"])
        self.assertTrue(ready["model"]["ok"])
        self.assertFalse(ready["voice"]["ok"])
        self.assertIn("typed input", ready["voice"]["detail"])

    def test_local_model_not_resident_blocks_model_layer(self):
        proxy = BrainSession()
        proxy._engine = "local"
        inner = _FakeInner(connected=True)
        inner._backend = "builtin"
        inner._llama = None
        inner.voice_ready = True
        proxy._inner = inner
        ready = proxy.readiness()
        self.assertTrue(ready["connection"]["ok"])
        self.assertFalse(ready["model"]["ok"])
        self.assertTrue(ready["voice"]["ok"])

    def test_local_ollama_model_layer_tracks_connection(self):
        proxy = BrainSession()
        proxy._engine = "local"
        inner = _FakeInner(connected=True)
        inner._backend = "ollama"
        inner.voice_ready = False
        proxy._inner = inner
        ready = proxy.readiness()
        self.assertTrue(ready["model"]["ok"])
        self.assertFalse(ready["voice"]["ok"])

    def test_lite_voice_tracks_mic_started(self):
        proxy = BrainSession()
        proxy._engine = "local"
        proxy._inner = LiteLiveSessionFake(mic_started=False)
        self.assertFalse(proxy.readiness()["voice"]["ok"])
        proxy._inner = LiteLiveSessionFake(mic_started=True)
        self.assertTrue(proxy.readiness()["voice"]["ok"])


class TestLocalVoiceReadyProperty(unittest.TestCase):
    def test_voice_ready_tracks_loaded_whisper(self):
        session = LocalLiveSession.__new__(LocalLiveSession)
        session._whisper = None
        self.assertFalse(session.voice_ready)
        session._whisper = object()
        self.assertTrue(session.voice_ready)


# ---------------------------------------------------------------- F04

class TestSendDelivery(unittest.TestCase):
    def test_proxy_no_inner_is_dropped(self):
        result = asyncio.run(BrainSession().send_text("hello"))
        self.assertEqual(result["status"], "dropped")
        self.assertEqual(result["reason"], "not connected")

    def test_proxy_dead_inner_is_dropped(self):
        proxy = BrainSession()
        proxy._inner = _FakeInner(connected=False)
        result = asyncio.run(proxy.send_text("hello"))
        self.assertEqual(result["status"], "dropped")

    def test_proxy_legacy_none_inner_reads_accepted(self):
        proxy = BrainSession()
        proxy._inner = _FakeInner(connected=True, send_result=None)
        result = asyncio.run(proxy.send_text("hello"))
        self.assertEqual(result["status"], "accepted")

    def test_proxy_propagates_inner_drop(self):
        proxy = BrainSession()
        proxy._inner = _FakeInner(
            connected=True,
            send_result={"status": "dropped",
                         "reason": "not connected"})
        result = asyncio.run(proxy.send_text("hello"))
        self.assertEqual(result["status"], "dropped")

    def test_groq_disconnected_is_dropped_not_silent(self):
        session = GroqSession()
        self.assertFalse(session.is_connected)
        result = asyncio.run(session.send_text("hello"))
        self.assertEqual(result["status"], "dropped")

    def test_local_disconnected_is_dropped_not_silent(self):
        session = LocalLiveSession()
        self.assertFalse(session.is_connected)
        result = asyncio.run(session.send_text("hello"))
        self.assertEqual(result["status"], "dropped")

    def test_lite_disconnected_is_dropped_not_silent(self):
        session = LiteLiveSession()
        self.assertFalse(session.is_connected)
        result = asyncio.run(session.send_text("hello"))
        self.assertEqual(result["status"], "dropped")

    def test_lite_blank_is_ignored_not_dropped(self):
        session = LiteLiveSession()
        session.is_connected = True
        result = asyncio.run(session.send_text("   "))
        self.assertEqual(result["status"], "ignored")


class TestGuiWiringByAst(unittest.TestCase):
    """GUI behaviour by AST (lab cannot import gui modules)."""

    def test_done_callback_surfaces_dropped_result(self):
        # GUI split #11 batch 2 INTENTIONAL: _on_typed_send_done moved
        # verbatim to window_session_typed.py.
        src = _gui_source("window_session_typed.py")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "_on_typed_send_done":
                body = ast.get_source_segment(src, node)
                break
        else:
            self.fail("_on_typed_send_done not found")
        self.assertIn('result.get("status") == "dropped"', body)
        self.assertIn("typed_send_failed", body)
        self.assertIn("Press Connect", body)

    def test_submit_time_branch_still_renders_not_duplicated(self):
        # F04 fills the gap; it must not duplicate the display
        # the submit-time branch already renders.
        # GUI split #11 batch 2 INTENTIONAL: _send_typed_message moved
        # verbatim to window_session_typed.py.
        src = _gui_source("window_session_typed.py")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "_send_typed_message":
                body = ast.get_source_segment(src, node)
                break
        else:
            self.fail("_send_typed_message not found")
        self.assertEqual(
            body.count("You're not connected yet"), 1)
        self.assertIn("input_cmd.setText(text)", body)

    def test_brain_engine_uses_explicit_resolver(self):
        # GUI split #4 INTENTIONAL: provider_resolution moved verbatim
        # to window_brain_engine.py. Assertions unchanged.
        src = _gui_source("window_brain_engine.py")
        self.assertIn("provider_resolution", src)
        self.assertNotIn(
            'engine if engine in ("gemini", "local") else "gemini"',
            src)

    def test_truth_line_names_frozen_override(self):
        # INTENTIONAL (refactor P1 step 5): _refresh_brain_truth moved
        # verbatim to window_settings_think.py. Assertions unchanged.
        src = _gui_source("window_settings_think.py")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) \
                    and node.name == "_refresh_brain_truth":
                body = ast.get_source_segment(src, node)
                break
        else:
            self.fail("_refresh_brain_truth not found")
        self.assertIn("frozen_override", body)
        self.assertIn("frozen", body)


if __name__ == "__main__":
    unittest.main()
