"""Find-glow guard (round-2 N6, fix #31).

Bug: a find jump opens a history window and glows the match
bubble (QTimer.singleShot(1200, _clear_find_glow)); Back to
latest clears the view and deletes that bubble; the timer then
fires on the deleted ChatBubble and Qt prints
  RuntimeError: libshiboken: Internal C++ object (ChatBubble)
  already deleted.
from _clear_find_glow — twice when two glows were pending
(a live-bubble jump followed by a stored-match jump).

Fix, two layers in the same direction:
  * _clear_chat_view_only() clears any find glow FIRST, while
    the glowing bubble still lives — which also nulls the ref,
    so a pending timer becomes a no-op.
  * _clear_find_glow() itself is guarded against a dead bubble
    (RuntimeError from setGraphicsEffect is swallowed, the ref
    still dropped), so a clear from any other path — or a timer
    that fires before the view clear runs — cannot raise either.

Follow-up (stale timer vs newer glow): every glow arms its
own 1200 ms timer, and a jump to a newer match replaces the
glow before the older timer fires. The older timer used to
clear the newer glow's effect early. Each glow now carries a
generation token: _glow_bubble bumps _find_glow_token and the
timer callback clears only when its token is still current.
A call with no token (view clear, direct clear, find close)
still clears unconditionally, as before.

Harness notes, both load-bearing:
  * These tests subclass tests.test_chat_ui.WindowBase (the real
    MainWindow offscreen, temp store) — the glow lives on the
    window's own bubbles, so a namespace stand-in cannot reach
    it.
  * Bare processEvents() does NOT deliver DeferredDelete events
    in this Qt build (proven while building the repro: a bare
    widget stays valid after deleteLater + processEvents, and
    dies only after an explicit sendPostedEvents(DeferredDelete)).
    The tests therefore flush deletions explicitly after Back to
    latest — that is what a running event loop does — and each
    test asserts the bubble IS deleted first, so a pass cannot
    be vacuous (a live bubble would never raise).
  * Qt prints slot tracebacks to file descriptor 2 (bypassing
    Python's sys.stderr object), so stderr is captured at the
    fd level with dup2 around the pump.

Qt tests need PySide6 (the .venv-qt interpreter); on an
interpreter without it they skip, following the programme's
standing optional-dependency skips.
"""

import os
import pathlib
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_chat_ui import WindowBase, _QT  # noqa: E402
try:
    import shiboken6  # noqa: E402
except Exception:  # noqa: BLE001 — PySide6 absent on sys
    shiboken6 = None


def _flush_deletions():
    """Deliver pending DeferredDelete events (what a running
    event loop does). See the module docstring: bare
    processEvents() does not deliver them here."""
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class _FdStderrCapture:
    """Capture fd 2 (where Qt prints slot tracebacks) to a
    temp file for the duration of the block."""

    def __enter__(self):
        self._tmp = tempfile.NamedTemporaryFile(
            delete=False, mode="w+b")
        self._tmp_path = self._tmp.name
        self._err_fd = os.open(self._tmp_path, os.O_WRONLY)
        self._saved_fd = os.dup(2)
        os.dup2(self._err_fd, 2)
        return self

    def __exit__(self, *exc):
        sys.stderr.flush()
        os.dup2(self._saved_fd, 2)
        os.close(self._err_fd)
        os.close(self._saved_fd)
        self._tmp.close()
        with open(self._tmp_path, "rb") as f:
            self.text = f.read().decode("utf-8", "replace")
        os.unlink(self._tmp_path)
        return False


@unittest.skipUnless(_QT, "Qt unavailable")
class FindGlowDeletedGuardTests(WindowBase):
    """The glow timer must never touch a deleted bubble."""

    def _pump(self, seconds=2.0):
        end = time.time() + seconds
        while time.time() < end:
            self.app.processEvents()
            time.sleep(0.05)
        self.app.processEvents()

    def _seed_capital(self):
        self._seed_exchange(question="What is the capital of France?",
                            answer="Paris")
        self._store.log_message(
            "user", "an older question about the capital")
        self._store.log_message(
            "assistant", "an older answer about the capital")

    def _seed_two_capitals(self):
        # Two live exchanges, each matching a different find
        # term and nothing in the older stored history (the
        # older rows mention only "the capital"), so each
        # _chat_find_reset jumps straight to a live bubble.
        self._seed_exchange(question="What is the capital of France?",
                            answer="Paris")
        self._seed_exchange(question="What is the capital of Germany?",
                            answer="Berlin")

    def test_timer_after_back_to_latest_leaves_no_traceback(self):
        # The exact round-2 sequence: find jump to a stored
        # match (history window + glow), Back to latest, then
        # the 1200 ms timer fires.
        self._seed_capital()
        with _FdStderrCapture() as cap:
            self.window._chat_find_reset("capital")
            self.assertTrue(self.window._btn_back_latest.isVisible())
            bubble = self.window._glowing_bubble
            self.assertIsNotNone(bubble)
            self.window._chat_back_to_latest()
            self.app.processEvents()
            _flush_deletions()
            # Precondition: the repro really reaches the deleted
            # state — without this the test would pass vacuously.
            self.assertFalse(shiboken6.isValid(bubble))
            self._pump(2.0)
        self.assertNotIn("already deleted", cap.text)
        self.assertNotIn("_clear_find_glow", cap.text)
        self.assertIsNone(self.window._glowing_bubble)

    def test_direct_clear_after_view_clear_does_not_raise(self):
        # The same deleted state, reached by a direct call
        # instead of the timer: _clear_find_glow must not raise.
        self._seed_capital()
        self.window._chat_find_reset("capital")
        bubble = self.window._glowing_bubble
        self.assertIsNotNone(bubble)
        self.window._chat_back_to_latest()
        self.app.processEvents()
        _flush_deletions()
        self.assertFalse(shiboken6.isValid(bubble))
        self.window._clear_find_glow()  # must not raise
        self.assertIsNone(self.window._glowing_bubble)

    def test_double_jump_leaves_no_traceback(self):
        # The round-2 "twice" shape: a live-bubble jump (glow +
        # timer) followed by a stored-match jump (window opens,
        # live bubble deleted, new glow + timer), then Back to
        # latest — two pending timers, zero tracebacks allowed.
        self._seed_capital()
        with _FdStderrCapture() as cap:
            self.window._chat_find_reset("france")
            live_bubble = self.window._glowing_bubble
            self.assertIsNotNone(live_bubble)
            self.window._chat_find_reset("capital")
            self.assertTrue(self.window._btn_back_latest.isVisible())
            window_bubble = self.window._glowing_bubble
            self.assertIsNotNone(window_bubble)
            self.window._chat_back_to_latest()
            self.app.processEvents()
            _flush_deletions()
            self.assertFalse(shiboken6.isValid(live_bubble))
            self.assertFalse(shiboken6.isValid(window_bubble))
            self._pump(2.0)
        self.assertNotIn("already deleted", cap.text)
        self.assertNotIn("_clear_find_glow", cap.text)
        self.assertIsNone(self.window._glowing_bubble)

    def test_glow_still_works_while_bubble_lives(self):
        # Control: the fix must not kill the glow itself. A
        # find jump to a live bubble sets the effect, and the
        # timer clears it cleanly once it fires.
        self._seed_capital()
        with _FdStderrCapture() as cap:
            self.window._chat_find_reset("france")
            bubble = self.window._glowing_bubble
            self.assertIsNotNone(bubble)
            self.assertIsNotNone(bubble.graphicsEffect())
            self._pump(2.0)
        self.assertIsNone(self.window._glowing_bubble)
        self.assertNotIn("already deleted", cap.text)

    def test_stale_timer_does_not_clear_newer_glow(self):
        # The follow-up race: jump A (live bubble, timer A
        # armed), then jump B before timer A fires. _glow_bubble
        # clears A's effect at once (correct); timer A must not
        # clear B's effect early. Timings: A at t=0, B at
        # t~0.6, timer A fires at t=1.2, timer B at t~1.8 — the
        # check at t~1.5 sits between the two firings.
        self._seed_two_capitals()
        with _FdStderrCapture() as cap:
            self.window._chat_find_reset("france")
            bubble_a = self.window._glowing_bubble
            self.assertIsNotNone(bubble_a)
            self.assertIsNotNone(bubble_a.graphicsEffect())
            self._pump(0.6)
            self.window._chat_find_reset("germany")
            bubble_b = self.window._glowing_bubble
            self.assertIsNotNone(bubble_b)
            self.assertIsNot(bubble_a, bubble_b)
            # Preconditions: the jump-B clear really ran, and
            # B's glow really was set — without these the
            # survival check below would pass vacuously.
            self.assertIsNone(bubble_a.graphicsEffect())
            self.assertIsNotNone(bubble_b.graphicsEffect())
            self._pump(0.9)
            self.assertIsNotNone(
                bubble_b.graphicsEffect(),
                "timer A cleared B's glow early")
            self.assertIs(self.window._glowing_bubble, bubble_b)
            # B's own timer must still clear it.
            self._pump(1.5)
            self.assertIsNone(self.window._glowing_bubble)
        self.assertNotIn("already deleted", cap.text)

    def test_two_stale_timers_do_not_clear_third_glow(self):
        # The same race across two generations: jumps A, B, C
        # in quick succession — both stale timers (A's and B's)
        # must leave C's glow alone until C's own timer fires.
        self._seed_two_capitals()
        self._seed_exchange(question="What is the capital of Italy?",
                            answer="Rome")
        with _FdStderrCapture() as cap:
            self.window._chat_find_reset("france")
            bubble_a = self.window._glowing_bubble
            self.assertIsNotNone(bubble_a)
            self._pump(0.3)
            self.window._chat_find_reset("germany")
            bubble_b = self.window._glowing_bubble
            self.assertIsNotNone(bubble_b)
            self.assertIsNot(bubble_a, bubble_b)
            self._pump(0.6)
            self.window._chat_find_reset("italy")
            bubble_c = self.window._glowing_bubble
            self.assertIsNotNone(bubble_c)
            self.assertIsNot(bubble_b, bubble_c)
            self.assertIsNotNone(bubble_c.graphicsEffect())
            # 0.9 s after C (1.8 s after A, 1.5 s after B):
            # both stale timers have fired (A's at 1.2 s, B's
            # at 1.5 s); C's own timer (1.2 s after C, i.e.
            # 2.1 s after A) has not.
            self._pump(0.9)
            self.assertIsNotNone(
                bubble_c.graphicsEffect(),
                "a stale timer cleared C's glow early")
            self.assertIs(self.window._glowing_bubble, bubble_c)
            self._pump(1.5)
            self.assertIsNone(self.window._glowing_bubble)
        self.assertNotIn("already deleted", cap.text)
