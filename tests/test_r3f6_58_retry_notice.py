"""R3F6 (#58): a connect retry shows on screen while CONNECTING shows.

Section-A finding A-RU4 (MINOR, PARTIAL): the 45 s envelope holds
live, but the retry notice reached console + activity.log only —
`_try_candidates` made no `_notify_status` call, so the on-screen
status stayed "CONNECTING" with no word that a retry was happening.

The fix: every retry point in the connect path notifies the screen
through the existing status channel (`_notify_status`, i.e. the
same `on_status_change` the window's status_changed signal feeds),
with a "CONNECTING: retrying — …" line, and `_on_status` shows
the retry words in the state label (pill stays "Connecting").
The 45 s envelope is untouched.

Session tests run on both interpreters (fake transport, real
`_try_candidates` — the driver58 shape). The GUI test is Qt-only
(the test_chat_ui WindowBase shape): the real MainWindow offscreen,
`_on_status` called directly, label + pill read back.
"""
import asyncio
import os
import pathlib
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]

from core.voice import live_client as _live_client  # noqa: E402
_TYPES = _live_client.types  # None on sys (google-genai absent) — the
# SDK-dependent session tests skip there (the test_textreplies shape);
# the helper + source-shape pins still run on both interpreters.


class _HangCM:
    async def __aenter__(self):
        await asyncio.sleep(3600)
        return object()

    async def __aexit__(self, *a):
        return False


class _RejectCM:
    async def __aenter__(self):
        raise Exception("404 NOT_FOUND: model 'model-a' not found.")

    async def __aexit__(self, *a):
        return False


class _AuthFailCM:
    async def __aenter__(self):
        raise Exception("401 UNAUTHENTICATED: API key not valid. Please pass a valid API key.")

    async def __aexit__(self, *a):
        return False


class _FakeLive:
    def __init__(self, cm):
        self._cm = cm

    def connect(self, model=None, config=None):
        return self._cm


class _FakeAio:
    def __init__(self, cm):
        self.live = _FakeLive(cm)


class _FakeClient:
    def __init__(self, cm):
        self.aio = _FakeAio(cm)


def _cfg():
    return _TYPES.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=_TYPES.AudioTranscriptionConfig())


async def _run_pass(cm, candidates, budget_s=None, deadline=None):
    from core.voice.live_client import GeminiLiveSession
    sess = GeminiLiveSession()
    sess._client = _FakeClient(cm)
    statuses: list[str] = []
    sess.on_status_change = statuses.append
    # Deadline computed after the session exists, so no setup
    # time is charged against it (the repro_58 lesson). An
    # explicit deadline wins — the envelope test passes ONE
    # shared deadline to two passes.
    if deadline is None:
        deadline = time.monotonic() + budget_s
    connected, timed_out, last = await sess._try_candidates(
        candidates, _cfg(), None, deadline=deadline)
    return sess, connected, statuses


def _retries(statuses):
    return [s for s in statuses if "retry" in s.lower()]


@unittest.skipIf(_TYPES is None, "google-genai unavailable on this interpreter")
class RetryNoticeSessionTests(unittest.TestCase):
    def test_timeout_retry_emits_on_screen_status(self):
        _s, _c, statuses = asyncio.run(_run_pass(
            _HangCM(), ["model-a", "model-b"], 3.0))
        retries = _retries(statuses)
        self.assertGreaterEqual(len(retries), 1, statuses)
        for s in retries:
            self.assertIn("CONNECTING", s.upper(), s)

    def test_rejection_retry_names_next_model(self):
        _s, _c, statuses = asyncio.run(_run_pass(
            _RejectCM(), ["model-a", "model-b"], 30.0))
        retries = _retries(statuses)
        self.assertGreaterEqual(len(retries), 1, statuses)
        self.assertTrue(any("model-b" in s for s in retries), retries)

    def test_auth_error_emits_no_retry_notice(self):
        try:
            _s, _c, statuses = asyncio.run(_run_pass(
                _AuthFailCM(), ["model-a", "model-b"], 30.0))
        except Exception:  # noqa: BLE001 — the 401 raise path
            return
        self.assertEqual(_retries(statuses), [], statuses)

    def test_envelope_still_45_and_shared(self):
        from core.voice.live_client_helpers import CONNECT_BUDGET_S
        self.assertEqual(CONNECT_BUDGET_S, 45.0)
        deadline = time.monotonic() + 3.0
        t0 = time.monotonic()
        asyncio.run(_run_pass(_HangCM(), ["model-a", "model-b"],
                              deadline=deadline))
        # Second pass against the SAME deadline: the budget is
        # spent, so it returns at once — cumulative time stays
        # inside the one shared deadline.
        asyncio.run(_run_pass(_HangCM(), ["model-a", "model-b"],
                              deadline=deadline))
        self.assertLess(time.monotonic() - t0, 3.5)

    def test_retry_notice_is_honest(self):
        _s, _c, statuses = asyncio.run(_run_pass(
            _RejectCM(), ["model-a", "model-b"], 30.0))
        retries = _retries(statuses)
        self.assertGreaterEqual(len(retries), 1, statuses)
        for s in retries:
            low = s.lower()
            self.assertTrue(any(w in low for w in (
                "timed out", "rejected", "refused", "couldn't", "didn't")), s)
            self.assertNotIn("50%", low, s)
            self.assertNotIn("almost", low, s)

class RetryNoticeStaticTests(unittest.TestCase):
    """Pins that need no SDK and no Qt — they run on both
    interpreters, so the suite still fails pre-fix on sys."""

    def test_notify_retry_helper_keeps_connecting_prefix(self):
        from core.voice.live_client import GeminiLiveSession
        sess = GeminiLiveSession()
        statuses: list[str] = []
        sess.on_status_change = statuses.append
        sess._notify_retry("the voice model timed out, trying the next one")
        self.assertEqual(
            statuses,
            ["CONNECTING: retrying — the voice model timed out, "
             "trying the next one"])

    def test_proxy_retry_branch_notifies(self):
        # The proxy retry in start() takes the same helper — the
        # one retry path no fake transport can drive directly.
        source = (ROOT / "core" / "voice" / "live_client_lifecycle.py"
                  ).read_text(encoding="utf-8")
        self.assertIn("_notify_retry(", source)
        self.assertIn("without the system proxy", source)


@unittest.skipUnless(_QT, "Qt unavailable")
class RetryNoticeGuiTests(WindowBase):
    def test_retry_status_shows_in_state_label(self):
        self.window._on_status(
            "CONNECTING: retrying — the voice model timed out, "
            "trying the next one")
        label = self.window.state_label.text()
        self.assertIn("timed out", label, label)
        self.assertNotEqual(label, "Connecting…", label)
        self.assertEqual(self.window.status_pill.text(), "Connecting")

    def test_plain_connecting_label_unchanged(self):
        self.window._on_status("CONNECTING")
        self.assertEqual(self.window.state_label.text(), "Connecting…")
        self.assertEqual(self.window.status_pill.text(), "Connecting")


if __name__ == "__main__":
    unittest.main()
