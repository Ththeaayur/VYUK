"""Round-3 section C fix queue item 11 (R1) — C2-F1 zombie hole.

A SIGKILLed Chrome whose parent never reaps it stays a zombie
(status "zombie", cmdline None), and the old _is_browser_cmdline(None)
returned True, so _chrome_running() stayed True and the attach
diagnosis still read "Chrome is running / 136 wall" for a dead
browser. The item-8 repro always wait()s after the kill, so it never
saw this shape — the re-verification caught it on the real path.

The fix has two defences: (1) a process whose status is "zombie"
is dead in _is_browser_cmdline / _chrome_running, distinguished
from the B7 seam case (a name-only mock, no status info) by STATUS,
not by cmdline; (2) _launch_chrome registers its Popen with
browser_cdp.note_launched_chrome, and _chrome_running() reaps
registered handles before it scans, so a VYUK-launched Chrome never
leaves an unreaped zombie behind.

No real Chrome and no Playwright import — the runner's interpreters
have no Playwright on their path, so a real-Chrome leg cannot run
here (the Finding-1 precedent: the repro holds the real-browser
proof, the suite pins the mechanism). The zombie state is simulated
by mocked psutil inputs; the reap is proven with a real subprocess.
"""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".pkgroot"))

try:
    import psutil as _psutil
except ImportError:  # noqa: BLE001 — sys interpreter has no psutil
    _psutil = None

from core.desktop import browser_cdp as cdp
from core.desktop import browser_session as bs
import core.desktop.browser_enable as be


def _fake_proc(name, cmdline=None, status=None, include_status=True):
    """A psutil-shaped fake: .info is a plain dict, exactly the
    shape process_iter(["name", "cmdline", "status"]) returns.
    The B7 seam shape omits cmdline AND status keys entirely."""
    proc = mock.Mock()
    info = {"name": name}
    if cmdline is not None or status is not None or include_status:
        info["cmdline"] = cmdline
    if include_status:
        info["status"] = status
    proc.info = info
    return proc


class IsBrowserCmdlineStatusTests(unittest.TestCase):
    """_is_browser_cmdline with the R1 status rule (pure, both)."""

    BROWSER = ["chrome-headless-shell", "--remote-debugging-port=9334",
               "--user-data-dir=/tmp/p", "about:blank"]
    HELPER = ["chrome-headless-shell", "--type=renderer",
              "--remote-debugging-port=9334"]

    def test_zombie_with_no_cmdline_is_dead(self):
        self.assertFalse(cdp._is_browser_cmdline(None, "zombie"))

    def test_zombie_with_browser_cmdline_is_dead(self):
        self.assertFalse(cdp._is_browser_cmdline(self.BROWSER, "zombie"))

    def test_zombie_with_helper_cmdline_is_dead(self):
        self.assertFalse(cdp._is_browser_cmdline(self.HELPER, "zombie"))

    def test_no_status_with_no_cmdline_is_browser(self):
        # The B7 seam rule, unchanged: absence of status info is
        # not evidence of being a helper — only an explicit
        # "zombie" status counts as dead.
        self.assertTrue(cdp._is_browser_cmdline(None, None))

    def test_no_status_with_no_cmdline_arg_is_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(None))

    def test_running_browser_cmdline_is_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(self.BROWSER, "running"))

    def test_running_helper_cmdline_is_not_browser(self):
        self.assertFalse(cdp._is_browser_cmdline(self.HELPER, "running"))

    def test_empty_cmdline_is_dead(self):
        self.assertFalse(cdp._is_browser_cmdline([], "running"))
        self.assertFalse(cdp._is_browser_cmdline([], None))

    def test_sleeping_status_with_no_cmdline_is_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(None, "sleeping"))


@unittest.skipUnless(_psutil is not None,
                     "psutil not installed for this interpreter")
class ChromeRunningZombieMockTests(unittest.TestCase):
    """_chrome_running with mocked psutil procs (Qt only)."""

    def _run(self, procs):
        with mock.patch.object(_psutil, "process_iter",
                               return_value=procs):
            return cdp._chrome_running()

    def test_zombie_only_is_dead(self):
        zombie = _fake_proc("chrome-headless-shell", None, "zombie")
        self.assertFalse(self._run([zombie]))

    def test_zombie_with_browser_cmdline_is_dead(self):
        zombie = _fake_proc(
            "chrome-headless-shell",
            ["chrome-headless-shell", "--remote-debugging-port=9334",
             "about:blank"], "zombie")
        self.assertFalse(self._run([zombie]))

    def test_live_browser_beside_zombie_counts(self):
        zombie = _fake_proc("chrome-headless-shell", None, "zombie")
        live = _fake_proc(
            "chrome-headless-shell",
            ["chrome-headless-shell", "--remote-debugging-port=9334",
             "about:blank"], "running")
        self.assertTrue(self._run([zombie, live]))

    def test_name_only_mock_still_browser(self):
        # The B7 seam shape: info carries name only, no cmdline
        # or status keys — must still count as a browser.
        proc = mock.Mock()
        proc.info = {"name": "chrome-headless-shell"}
        self.assertTrue(self._run([proc]))

    def test_helpers_alone_do_not_count(self):
        helper = _fake_proc(
            "chrome-headless-shell",
            ["chrome-headless-shell", "--type=renderer"], "running")
        self.assertFalse(self._run([helper]))

    def test_no_chrome_is_dead(self):
        self.assertFalse(self._run(
            [_fake_proc("firefox", None, "running")]))


class ReapLaunchedChromeTests(unittest.TestCase):
    """The Popen registry: register, reap, and the _launch_chrome
    store. Runs on both interpreters (no psutil needed)."""

    def setUp(self):
        self._saved = list(cdp._LAUNCHED_CHROME_PROCS)
        cdp._LAUNCHED_CHROME_PROCS[:] = []

    def tearDown(self):
        cdp._LAUNCHED_CHROME_PROCS[:] = self._saved

    def test_reap_removes_exited_handle(self):
        proc = mock.Mock()
        proc.poll.return_value = -9
        cdp.note_launched_chrome(proc)
        cdp._reap_launched_chrome()
        proc.wait.assert_called_once()
        self.assertEqual(cdp._LAUNCHED_CHROME_PROCS, [])

    def test_reap_keeps_live_handle(self):
        proc = mock.Mock()
        proc.poll.return_value = None
        cdp.note_launched_chrome(proc)
        cdp._reap_launched_chrome()
        proc.wait.assert_not_called()
        self.assertEqual(cdp._LAUNCHED_CHROME_PROCS, [proc])

    def test_reap_never_raises(self):
        bad = mock.Mock()
        bad.poll.side_effect = RuntimeError("boom")
        cdp.note_launched_chrome(bad)
        cdp._reap_launched_chrome()  # must not raise
        self.assertEqual(cdp._LAUNCHED_CHROME_PROCS, [])

    def test_real_killed_child_is_reaped(self):
        # A real subprocess killed without wait() is a zombie child
        # of this process until somebody reaps it. Registering it
        # and calling _reap_launched_chrome must reap it: a second
        # waitpid then raises ChildProcessError (already reaped),
        # instead of returning the zombie's status.
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            child.kill()
            # The kill is async: wait until the child has actually
            # died (poll() is not None) before testing the reap —
            # the reap, not the kill timing, is what's under test.
            deadline = time.monotonic() + 5.0
            while child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(child.poll())
            cdp.note_launched_chrome(child)
            cdp._reap_launched_chrome()
            self.assertEqual(cdp._LAUNCHED_CHROME_PROCS, [])
            with self.assertRaises(ChildProcessError):
                os.waitpid(child.pid, os.WNOHANG)
        finally:
            try:
                child.wait(timeout=5)
            except Exception:  # noqa: BLE001 — already reaped/gone
                pass

    def test_launch_chrome_registers_its_popen(self):
        fake = mock.Mock()
        fake.poll.return_value = None
        profile = Path(tempfile.mkdtemp(prefix="r1_profile_"))
        with mock.patch("subprocess.Popen", return_value=fake), \
                mock.patch.object(be, "_debug_profile_dir",
                                  return_value=profile):
            self.assertTrue(be._launch_chrome("/bin/true", 9334))
        self.assertIn(fake, cdp._LAUNCHED_CHROME_PROCS)


@unittest.skipUnless(_psutil is not None,
                     "psutil not installed for this interpreter")
class AttachFailureZombieTests(unittest.TestCase):
    """The diagnosis end to end at the function level (Qt only):
    port silent + zombie-only scan reads dead, port silent + a live
    browser still reads the 136 wall."""

    def _diagnosis(self, procs):
        exc = Exception("BrowserType.connect_over_cdp: connect "
                        "ECONNREFUSED ::1:9334")
        with mock.patch.object(bs, "_cdp_port_answers",
                               return_value=False), \
                mock.patch.object(_psutil, "process_iter",
                                  return_value=procs):
            return bs._attach_failure(9334, "http://localhost:9334",
                                      exc)

    def test_zombie_only_reads_dead(self):
        zombie = _fake_proc("chrome-headless-shell", None, "zombie")
        msg = self._diagnosis([zombie])
        self.assertIn("Chrome isn't running", msg)
        self.assertNotIn("Chrome is running", msg)
        self.assertIn("9334", msg)

    def test_live_browser_still_reads_wall(self):
        live = _fake_proc(
            "chrome-headless-shell",
            ["chrome-headless-shell", "--remote-debugging-port=9334",
             "about:blank"], "running")
        msg = self._diagnosis([live])
        self.assertIn("Chrome is running", msg)
        self.assertIn("136 wall", msg)


if __name__ == "__main__":
    unittest.main()
