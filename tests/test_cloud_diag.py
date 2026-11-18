"""Cloud diagnostics (Fix 5, 2026-11-05) — ONE probe, TWO doors.

What this suite proves, with fakes only (no real Google endpoint,
no real key, no network, no Qt):
(a) the diagnostic tests the REST door and the Live door separately
    — a REST pass with a Live refusal is NOT "key works";
(b) each Live failure class classifies distinctly — model-not-
    supported (bidiGenerateContent), quota (429 / RESOURCE_EXHAUSTED),
    auth, network — and carries Google's verbatim text;
(c) the key tier is "unknown" unless a quota error was actually
    observed, and even then the tier stays "unknown" with a note —
    never a guessed free/paid;
(d) a refused model does not burn reconnect attempts (model and
    quota failures are fatal for the auto-reconnect episode);
(e) a network failure is never reported as a key failure;
(f) the brain fallback is announced (FALLBACK status -> friendly
    fallback text -> chat), never silent — pinned, not rebuilt;
(g) no diagnostic output ever contains the key (redaction);
(h) the exhaustion report names the classified kind and carries
    the verbatim (300-char cap), not the old 180-char swallow,
    and a quota refusal is named LIVE_QUOTA, not MODEL_REJECTED;
(i) the Doctor wires the new check into its check list.

Harness: fake genai clients (models.get returns a fake Model with
a chosen supported_actions list, or raises a fake API error with
a chosen code/message). The real cloud_diag, live_client_error_cost,
live_client_send mixin, and window_widgets_helpers modules are
imported — only the Google side is faked.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.voice import cloud_diag as cd
from core.voice.live_client_error_cost import _classify_key_error
from core.voice.live_client_send import GeminiLiveSessionSendMixin

try:
    from gui import window_widgets_helpers as _wh
except Exception:  # noqa: BLE001 — GUI helpers need the style package
    _wh = None

FAKE_KEY = "AIzaSyD-FAKEKEY1234567890abcdefg"
MODEL = "gemini-3.8-live"


class _FakeApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class _FakeModels:
    def __init__(self, model=None, error=None):
        self._model = model
        self._error = error

    def get(self, model=None):
        if self._error is not None:
            raise self._error
        return self._model


class _FakeClient:
    def __init__(self, model=None, error=None):
        self.models = _FakeModels(model=model, error=error)


def _fake_model(actions):
    return SimpleNamespace(supported_actions=list(actions),
                            name="models/" + MODEL)


def _diag(model=None, error=None, key=FAKE_KEY):
    factory = lambda k: _FakeClient(model=model, error=error)
    return cd.diagnose_cloud(key, MODEL, client_factory=factory)


class DiagnoseDoorsTests(unittest.TestCase):
    def test_rest_pass_live_pass_reports_both_doors(self):
        report = _diag(model=_fake_model(["generateContent",
                                          "bidiGenerateContent"]))
        self.assertEqual(report["rest"]["status"], "pass")
        self.assertEqual(report["live"]["status"], "pass")
        self.assertEqual(report["live"]["kind"], "ok")
        self.assertIn("REST", report["summary"])
        self.assertIn("Live", report["summary"])

    def test_rest_pass_live_refused_is_not_key_works(self):
        # The L1 loophole: models.get succeeds (REST) while the
        # model does not list bidiGenerateContent (Live refused).
        report = _diag(model=_fake_model(["generateContent"]))
        self.assertEqual(report["rest"]["status"], "pass")
        self.assertEqual(report["live"]["status"], "fail")
        self.assertEqual(report["live"]["kind"], "model")
        self.assertIn("bidiGenerateContent", report["live"]["detail"])

    def test_live_support_unknown_when_list_absent(self):
        # An absent supported_actions list is missing evidence,
        # not a refusal — it must never be reported as a failure.
        report = _diag(model=SimpleNamespace(name="models/" + MODEL))
        self.assertEqual(report["rest"]["status"], "pass")
        self.assertEqual(report["live"]["status"], "unknown")
        self.assertEqual(report["live"]["kind"], "unknown")


class ClassifyTests(unittest.TestCase):
    def test_bidi_refusal_is_model_not_auth(self):
        exc = _FakeApiError(
            404, "404 NOT_FOUND: Model gemini-live-2.5-flash-native-audio "
                 "is not found for API version v1beta, not supported "
                 "for bidiGenerateContent")
        got = cd.classify_live_error(exc)
        self.assertEqual(got["kind"], "model")
        self.assertIn("bidiGenerateContent", got["verbatim"])
        self.assertIn("voice model", got["caption"])

    def test_quota_429_is_quota_not_auth_or_unknown(self):
        exc = _FakeApiError(429, "429 RESOURCE_EXHAUSTED: Quota "
                                 "exceeded for quota metric "
                                 "'GenerateContent requests'")
        got = cd.classify_live_error(exc)
        self.assertEqual(got["kind"], "quota")
        self.assertIn("quota", got["caption"].lower())
        # The shared classifier agrees — the L2 fix.
        self.assertEqual(_classify_key_error(exc), "quota")

    def test_quota_wording_without_code_is_quota(self):
        exc = Exception("Quota exceeded for this key today")
        self.assertEqual(cd.classify_live_error(exc)["kind"], "quota")

    def test_auth_stays_auth(self):
        exc = _FakeApiError(401, "401 UNAUTHENTICATED: API key not "
                                 "valid. Please pass a valid API key.")
        self.assertEqual(cd.classify_live_error(exc)["kind"], "auth")

    def test_network_is_not_a_key_failure(self):
        exc = ConnectionError("getaddrinfo failed: Name or service "
                              "not known")
        got = cd.classify_live_error(exc)
        self.assertEqual(got["kind"], "network")
        self.assertNotIn("key", got["caption"].lower()
                         .replace("key has", ""))

    def test_unknown_keeps_verbatim_never_guesses(self):
        exc = Exception("some brand-new server wording")
        got = cd.classify_live_error(exc)
        self.assertEqual(got["kind"], "unknown")
        self.assertIn("brand-new server wording", got["verbatim"])


class DiagnoseFailureTests(unittest.TestCase):
    def test_quota_failure_marks_both_doors_quota(self):
        exc = _FakeApiError(429, "429 RESOURCE_EXHAUSTED: Quota "
                                 "exceeded")
        report = _diag(error=exc)
        self.assertEqual(report["rest"]["kind"], "quota")
        self.assertEqual(report["live"]["status"], "fail")
        self.assertEqual(report["live"]["kind"], "quota")
        self.assertEqual(report["tier"], "unknown")
        self.assertIn("billing", report["tier_note"])

    def test_network_failure_leaves_live_unknown_not_failed(self):
        exc = ConnectionError("getaddrinfo failed")
        report = _diag(error=exc)
        self.assertEqual(report["rest"]["kind"], "network")
        self.assertEqual(report["live"]["status"], "unknown")
        self.assertEqual(report["tier"], "unknown")
        self.assertIn("does not expose", report["tier_note"])

    def test_auth_failure_marks_both_doors_auth(self):
        exc = _FakeApiError(401, "API key not valid")
        report = _diag(error=exc)
        self.assertEqual(report["rest"]["kind"], "auth")
        self.assertEqual(report["live"]["status"], "fail")
        self.assertEqual(report["live"]["kind"], "auth")

    def test_no_key_reports_auth_and_live_untested(self):
        report = cd.diagnose_cloud("", MODEL)
        self.assertEqual(report["rest"]["kind"], "auth")
        self.assertEqual(report["live"]["status"], "unknown")

    def test_tier_is_unknown_on_a_clean_pass(self):
        report = _diag(model=_fake_model(["bidiGenerateContent"]))
        self.assertEqual(report["tier"], "unknown")
        self.assertIn("does not expose", report["tier_note"])


class RedactionTests(unittest.TestCase):
    def test_key_never_appears_in_any_output(self):
        # An exception whose text embeds the key: the verbatim
        # and every door detail must come back redacted.
        exc = Exception(f"request failed for key {FAKE_KEY} "
                        "with code 429 quota exceeded")
        report = _diag(error=exc)
        blob = (str(report["rest"]) + str(report["live"])
                + report["summary"] + report["tier_note"])
        self.assertNotIn(FAKE_KEY, blob)
        self.assertNotIn("AIzaSyD", blob)

    def test_redact_strips_key_equals_forms(self):
        text = cd._redact("api_key=AIzaSyD-FAKEKEY1234567890abcdefg "
                          "and done")
        self.assertNotIn("AIzaSyD", text)
        self.assertIn("[key redacted]", text)


class ExhaustionTests(unittest.TestCase):
    def _reporter(self):
        class _Reporter(GeminiLiveSessionSendMixin):
            def __init__(self):
                self.statuses = []

            def _notify_status(self, status):
                self.statuses.append(status)

        return _Reporter()

    def test_quota_named_live_quota_not_model_rejected(self):
        exc = _FakeApiError(429, "429 RESOURCE_EXHAUSTED: Quota "
                                 "exceeded for Live requests")
        rep = self._reporter()
        rep._report_exhaustion([MODEL], [], exc)
        self.assertEqual(len(rep.statuses), 1)
        self.assertIn("LIVE_QUOTA", rep.statuses[0])
        self.assertNotIn("MODEL_REJECTED", rep.statuses[0])
        self.assertIn("RESOURCE_EXHAUSTED", rep.statuses[0])

    def test_model_rejection_still_named_model_rejected(self):
        exc = _FakeApiError(404, "404 NOT_FOUND: Model not found "
                                 "for bidiGenerateContent")
        rep = self._reporter()
        rep._report_exhaustion([MODEL], [], exc)
        self.assertIn("MODEL_REJECTED", rep.statuses[0])
        self.assertIn("bidiGenerateContent", rep.statuses[0])

    def test_verbatim_not_swallowed_at_180_chars(self):
        # The old cap cut Google's reply at 180 chars in the
        # tooltip; the Fix-5 cap is 300.
        long_text = "x" * 250
        exc = Exception("Model refused: " + long_text)
        rep = self._reporter()
        rep._report_exhaustion([MODEL], [], exc)
        self.assertIn("x" * 200, rep.statuses[0])

    def test_pure_timeout_still_names_network_path(self):
        rep = self._reporter()
        rep._report_exhaustion([MODEL], [MODEL], None)
        self.assertIn("Timed out", rep.statuses[0])
        self.assertIn("network path", rep.statuses[0].lower())


@unittest.skipIf(_wh is None, "window_widgets_helpers unavailable")
class FriendlyErrorTests(unittest.TestCase):
    def test_quota_has_its_own_plain_words(self):
        text = _wh._friendly_error(
            "ERROR: LIVE_QUOTA: Google refused with 429 quota")
        self.assertIn("quota", text.lower())
        self.assertIn("billing", text.lower())
        self.assertNotIn("Something went wrong", text)

    def test_bidi_refusal_has_its_own_plain_words(self):
        text = _wh._friendly_error(
            "ERROR: MODEL_REJECTED: not supported for "
            "bidiGenerateContent")
        self.assertIn("bidiGenerateContent", text)
        self.assertNotIn("Something went wrong", text)

    def test_quota_is_fatal_for_auto_reconnect(self):
        # L6: a quota refusal must not burn the 2/5/15/30 s
        # reconnect episode against a server-side limit.
        self.assertTrue(_wh._is_fatal_error(
            "ERROR: LIVE_QUOTA: 429 RESOURCE_EXHAUSTED"))
        self.assertTrue(_wh._is_fatal_error(
            "ERROR: quota exceeded for this key"))

    def test_model_rejection_still_fatal(self):
        self.assertTrue(_wh._is_fatal_error(
            "ERROR: MODEL_REJECTED: Google refused every model"))

    def test_plain_timeout_still_not_fatal(self):
        self.assertFalse(_wh._is_fatal_error(
            "ERROR: Timed out reaching Google's voice service"))


class FallbackAnnouncedTests(unittest.TestCase):
    def test_fallback_status_is_announced_not_silent(self):
        # L7 pinned, not rebuilt: the FALLBACK notice path exists
        # in window_session_status (chat + caption) and the reason
        # is emitted by brain_session_core.
        src = (ROOT / "gui" / "window_session_status.py").read_text(
            encoding="utf-8")
        self.assertIn('upper.startswith("FALLBACK")', src)
        self.assertIn("_friendly_fallback(reason)", src)
        self.assertIn("self._add_system(friendly)", src)
        brain = (ROOT / "core" / "brain"
                 / "brain_session_core.py").read_text(encoding="utf-8")
        self.assertIn('on_status_change(f"FALLBACK: {reason}")', brain)


class DoctorWiringTests(unittest.TestCase):
    def test_doctor_registers_cloud_doors_check(self):
        src = (ROOT / "doctor.py").read_text(encoding="utf-8")
        self.assertIn("def check_cloud_doors", src)
        self.assertIn("check_cloud_doors", src.split("_CHECKS = [")[1])
        self.assertIn("diagnose_cloud", src)
        tree = ast.parse(src)
        names = {node.name for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
        self.assertIn("check_cloud_doors", names)


class CostRecordFailureTests(unittest.TestCase):
    def test_lost_live_cost_row_is_logged_and_marked_degraded(self):
        # Field-check fix #2 (T7 #20): a Live cost row lost to
        # a ledger failure is logged and the ledger is marked
        # degraded — the cap totals undercount, so the loss must
        # be visible instead of swallowed by `except: pass`.
        from core import cost_ledger as cost_mod
        from core.voice import live_client_error_cost as costmod

        cost_mod.reset_live_record_degraded()
        self.addCleanup(cost_mod.reset_live_record_degraded)
        real_ledger = costmod._COST_LEDGER

        class _FailingLedger:
            def record_live_turn(self, model, usage=None):
                raise RuntimeError("ledger down")

        costmod._COST_LEDGER = _FailingLedger()
        self.addCleanup(lambda: setattr(costmod, "_COST_LEDGER",
                                        real_ledger))
        lines: list[str] = []
        with mock.patch.object(costmod, "_log_line",
                               lambda msg: lines.append(msg)):
            costmod._cost_record_live(
                SimpleNamespace(usage_metadata=None),
                model="test-model")  # must not raise
        self.assertTrue(
            any("cost" in line.lower() and "lost" in line.lower()
                for line in lines),
            f"no cost-loss log line in {lines!r}")
        state = cost_mod.live_record_degraded_state()
        self.assertIn("live", state)
        self.assertGreaterEqual(state["live"]["count"], 1)
        self.assertIn("ledger down", state["live"]["last_error"])

    def test_live_record_degraded_state_is_empty_when_healthy(self):
        from core import cost_ledger as cost_mod

        cost_mod.reset_live_record_degraded()
        self.addCleanup(cost_mod.reset_live_record_degraded)
        self.assertEqual(cost_mod.live_record_degraded_state(), {})


if __name__ == "__main__":
    unittest.main()
