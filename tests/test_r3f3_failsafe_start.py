"""R3F3: the failsafe watchdog must not arm its trip clock before the GUI
can beat (round-3 fix #3).

Root cause (proven by instrumented full-suite traces): failsafe.start()
runs during MainWindow.__init__ (service host) and armed _last_beat=now,
but the 2 s beat timer cannot fire until the event loop pumps after the
window is shown. When a build took >10 s (slow VM / heavy launch), the
monitor tripped a healthy app ("no heartbeat for over 10.0s — the app
may be hung"), posted the trip line into the chat as a system node, and
— landing mid-test — flipped visible_thread()[-1] to a "done" node:
the Stop test's 'done' != 'stopped' (twice in the wild) and the
regenerate test's 1 != 2 (repro run 7). The fix: start() leaves the
clock disarmed (None) until the first beat(); the monitor already skips
a None clock, and a hang after beats flow trips exactly as before.
"""
import time
import unittest

from core import failsafe


class FailsafeStartTests(unittest.TestCase):
    TIMEOUT = 0.5
    POLL = 0.05
    WAIT = 1.2  # >2x the timeout: the monitor has polled many times

    def setUp(self):
        failsafe.configure(timeout_s=self.TIMEOUT, poll_s=self.POLL)
        self.assertTrue(
            failsafe.reset(),
            "stop-flag file present — cannot re-arm in this tree")

    def tearDown(self):
        failsafe.stop()
        failsafe.reset()
        failsafe.configure(timeout_s=10.0, poll_s=0.25)

    def test_start_does_not_arm_until_first_beat(self):
        # A slow window build: start() then no beats for over the
        # timeout — the event loop isn't pumping yet. Must not trip.
        failsafe.start()
        time.sleep(self.WAIT)
        self.assertFalse(
            failsafe.tripped(),
            "start() armed the clock before any beat — a slow build "
            "trips a healthy app")

    def test_beat_arms_and_timeout_still_trips(self):
        # Once beats flow and then stop, the watchdog must still trip:
        # the fix disarms the clock, it does not disable the watchdog.
        failsafe.start()
        failsafe.beat()
        time.sleep(self.WAIT)
        self.assertTrue(
            failsafe.tripped(),
            "no trip after beats stopped — the watchdog is dead")

    def test_restart_disarms_until_next_beat(self):
        # Every window build re-starts (service host, per window):
        # a re-start must disarm again until the next beat, or the
        # second build trips on the first window's stale clock.
        failsafe.start()
        failsafe.beat()
        failsafe.start()
        time.sleep(self.WAIT)
        self.assertFalse(
            failsafe.tripped(),
            "re-start kept the old clock — a slow rebuild trips")


if __name__ == "__main__":
    unittest.main()
