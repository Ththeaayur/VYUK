"""Mic starvation signal (mic field check, report §9) — tests for
the send loop's silent-deaf failure.

The failure: `_send_audio_loop` slept 10 ms and continued when
`mic_queue` was empty — forever, with no log line, no counter,
no status. A session with a dead/muted/never-opened mic looked
exactly like a connected session whose user simply had not
spoken yet.

The fix: the loop tracks consecutive seconds with an empty
queue while the session is live and `_last_voice_at` is stale;
after 20 s it logs `[Send Audio] no mic audio for Ns`
(throttled, then every 20 s) and exposes the starved seconds
on the session as `_mic_starved_seconds`, so the UI can show
it. A frame arriving clears all three fields.

Lab notes: the thresholds are module constants patched small
here (B16 pattern, same as the micfix send tests) so the tests
run in under a second — the production default is 20 s. The
log line is captured by patching `lc_recv._log_line` with a
recorder, so the tests never touch data/activity.log. The
send path is only exercised by the reset test, with a silence
chunk (RMS 0, so no voice stamp and no filler touch) and a
no-op send.
"""

import asyncio
import queue
import time
import unittest
from types import SimpleNamespace


class MicStarvationTests(unittest.TestCase):
    def _drive(self, stub_self, fake_audio, seconds,
               background=None):
        from core.voice import live_client as lc

        async def run():
            task = asyncio.create_task(
                lc.GeminiLiveSession._send_audio_loop(
                    stub_self, fake_audio))
            extra = None
            if background is not None:
                extra = asyncio.create_task(background())
            await asyncio.sleep(seconds)
            task.cancel()
            if extra is not None:
                extra.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            if extra is not None:
                try:
                    await extra
                except asyncio.CancelledError:
                    pass

        asyncio.run(run())

    def test_empty_queue_logs_and_exposes_starved_seconds(self):
        from core.voice import live_client_receive as lc_recv

        lines = []
        orig_log = lc_recv._log_line
        orig_after = lc_recv._MIC_STARVED_AFTER_S
        orig_every = lc_recv._MIC_STARVED_LOG_EVERY_S
        lc_recv._log_line = lines.append
        lc_recv._MIC_STARVED_AFTER_S = 0.2
        lc_recv._MIC_STARVED_LOG_EVERY_S = 0.2
        try:
            fake_audio = SimpleNamespace(mic_queue=queue.Queue())
            stub_self = SimpleNamespace(
                _running=True,
                _session=SimpleNamespace(),
                _last_voice_at=None)
            self._drive(stub_self, fake_audio, 0.7)
            # The signal fired: at least one line, in the
            # report's exact words, and the session exposes
            # the starved seconds.
            self.assertGreaterEqual(len(lines), 1)
            self.assertTrue(all(
                line.startswith("[Send Audio] no mic audio for ")
                and line.endswith("s") for line in lines))
            self.assertGreaterEqual(
                getattr(stub_self, "_mic_starved_seconds", 0.0),
                0.2)
            # Issue-hunt #5: the signal is mirrored onto the
            # audio manager, so mic_status() can carry it.
            self.assertGreaterEqual(
                getattr(fake_audio, "_mic_starved_seconds", 0.0),
                0.2)
            # Throttled: an unthrottled loop would log once per
            # 10 ms poll (~50 lines in 0.5 s of starvation).
            self.assertLessEqual(len(lines), 5)
        finally:
            lc_recv._log_line = orig_log
            lc_recv._MIC_STARVED_AFTER_S = orig_after
            lc_recv._MIC_STARVED_LOG_EVERY_S = orig_every

    def test_frame_arriving_clears_starved_seconds(self):
        from core.voice import live_client_receive as lc_recv

        class Blob:
            def __init__(self, data=None, mime_type=None):
                self.data = data
                self.mime_type = mime_type

        lines = []
        orig_log = lc_recv._log_line
        orig_after = lc_recv._MIC_STARVED_AFTER_S
        orig_every = lc_recv._MIC_STARVED_LOG_EVERY_S
        orig_types = lc_recv.types
        lc_recv._log_line = lines.append
        lc_recv._MIC_STARVED_AFTER_S = 0.2
        lc_recv._MIC_STARVED_LOG_EVERY_S = 0.2
        lc_recv.types = SimpleNamespace(Blob=Blob)
        try:
            async def ok_send(audio=None):
                return None

            fake_audio = SimpleNamespace(mic_queue=queue.Queue())
            stub_self = SimpleNamespace(
                _running=True,
                _session=SimpleNamespace(
                    send_realtime_input=ok_send),
                _last_voice_at=None)
            state = {}

            async def feed_frame():
                await asyncio.sleep(0.35)
                # The signal must be active before the frame
                # arrives, or the reset proves nothing.
                state["before"] = getattr(
                    stub_self, "_mic_starved_seconds", 0.0)
                fake_audio.mic_queue.put(b"\x00\x00" * 320)

            # One run: starve, fire, feed a silence frame, end
            # 0.1 s later — inside the next threshold window,
            # so a re-fire cannot mask the reset. (A second
            # drive would re-initialise the state at the top
            # of the loop and prove nothing.)
            self._drive(stub_self, fake_audio, 0.45,
                        background=feed_frame)
            self.assertGreaterEqual(state.get("before", 0.0), 0.2)
            self.assertGreaterEqual(len(lines), 1)
            self.assertEqual(
                getattr(stub_self, "_mic_starved_seconds", None),
                0.0)
            # Issue-hunt #5: the mirror clears with the signal.
            self.assertEqual(
                getattr(fake_audio, "_mic_starved_seconds", None),
                0.0)
            # At most the pre-frame firing(s): no line was
            # logged after the frame cleared the signal.
            self.assertLessEqual(len(lines), 2)
        finally:
            lc_recv._log_line = orig_log
            lc_recv._MIC_STARVED_AFTER_S = orig_after
            lc_recv._MIC_STARVED_LOG_EVERY_S = orig_every
            lc_recv.types = orig_types

    def test_recent_voice_suppresses_the_signal(self):
        from core.voice import live_client_receive as lc_recv

        lines = []
        orig_log = lc_recv._log_line
        orig_after = lc_recv._MIC_STARVED_AFTER_S
        orig_every = lc_recv._MIC_STARVED_LOG_EVERY_S
        lc_recv._log_line = lines.append
        lc_recv._MIC_STARVED_AFTER_S = 0.2
        lc_recv._MIC_STARVED_LOG_EVERY_S = 0.2
        try:
            fake_audio = SimpleNamespace(mic_queue=queue.Queue())
            stub_self = SimpleNamespace(
                _running=True,
                _session=SimpleNamespace(),
                _last_voice_at=None)

            async def refresh_voice():
                while True:
                    stub_self._last_voice_at = time.monotonic()
                    await asyncio.sleep(0.05)

            # The queue stays empty for 0.7 s, but voice was
            # heard a moment ago throughout — the stale guard
            # must hold the signal back entirely.
            self._drive(stub_self, fake_audio, 0.7,
                        background=refresh_voice)
            self.assertEqual(lines, [])
            self.assertEqual(
                getattr(stub_self, "_mic_starved_seconds", None),
                0.0)
            # Issue-hunt #5: the mirror never fires either.
            self.assertEqual(
                getattr(fake_audio, "_mic_starved_seconds", None),
                0.0)
        finally:
            lc_recv._log_line = orig_log
            lc_recv._MIC_STARVED_AFTER_S = orig_after
            lc_recv._MIC_STARVED_LOG_EVERY_S = orig_every


if __name__ == "__main__":
    unittest.main()
