"""Round-3 C2-F1 (2026-11-20): the attach-failure diagnosis misread
a just-killed Chrome as "running".

The bug (section C, parent-confirmed in code AND in the raw C2-6
verdict JSON): ``_chrome_running()`` was a psutil process-NAME scan
— it reported Chrome as "running" when a process with a Chrome-ish
name existed, even when the Chrome that mattered (the one with the
CDP port open) had just been killed. SIGKILLing the headless-shell
browser leaves its helpers (renderer / gpu / zygote / ...) alive
for a moment, all still named chrome — the diagnosis then took the
"running" branch and blamed the Chrome 136 wall for a dead browser.
The exact pre-fix wording, from the C2-6 verdict: "Couldn't attach
to Chrome at http://localhost:9334 (BrowserType.connect_over_cdp:
connect ECONNREFUSED::1:9334). Chrome is running, and this is most
likely the Chrome 136 wall: ...".

The fix (parent ruling): probe the CDP port FIRST — the diagnosis
is grounded in whether the endpoint answers, not in whether a
process name exists. When the port does not answer, the diagnosis
says Chrome is not reachable / dead, with the port named — never
"running". The process scan stays as the secondary signal, but it
must never OVERRULE the probe, and it now requires a
browser-shaped command line (no --type= helper flag, positional
arguments a URL or a path), so orphaned helpers no longer read as
a running browser.

The full end-to-end proof with a REAL browser (a live headless
Chrome is attached, SIGKILLed, and the diagnosis is sampled in a
tight loop while the orphans are still alive — plus a stray
chrome-named sleep process that must not overrule the dead port)
lives in the repro ``r3c2f1_attach_work/repro_c2f1.py`` (REPRO-EXIT 1
pre-fix with exactly ``killed_diagnosis_names_dead`` +
``stray_chrome_name_does_not_overrule_probe`` failing, REPRO-EXIT 0
post-fix). This suite pins the same mechanism WITHOUT a browser —
the runner's interpreters have no Playwright on their path, so a
real-Chrome leg cannot run here (the Finding-1 precedent). The
orphan state is simulated by the mocked inputs: a probe that does
not answer + a name scan that still sees chrome-named processes.

The suite does not drive execute() and does not dirty data/.
"""

import http.server
import socket
import threading
import unittest
from unittest import mock

from core.desktop import browser_cdp as cdp
from core.desktop import browser_session as bs


class _Sentinel:
    pass


_SENTINEL = _Sentinel()


def _fake_proc(name, cmdline=_SENTINEL):
    """A mock psutil process with the given name/cmdline. The
    _SENTINEL default (no cmdline key at all) is the B7 seam shape:
    ``proc.info`` with only a name, as test_chrome136 builds it."""
    proc = mock.Mock()
    if cmdline is _SENTINEL:
        proc.info = {"name": name}
    else:
        proc.info = {"name": name, "cmdline": cmdline}
    return proc


_CHROME = "/usr/bin/chrome"
_BROWSER_CMDLINE = [_CHROME, "--remote-debugging-port=9334",
                    "--no-first-run", "--user-data-dir=/tmp/dbg",
                    "about:blank"]
_CHILD_CMDLINE = [_CHROME, "--type=renderer",
                  "--user-data-dir=/tmp/dbg", "--mojo-application-channel-token=abc"]
_FAKE_CMDLINE = ["/bin/sleep", "3600"]


class IsBrowserCmdlineTests(unittest.TestCase):
    def test_browser_launch_with_flags_and_url_is_a_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(_BROWSER_CMDLINE))

    def test_browser_launch_with_flags_only_is_a_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(
            [_CHROME, "--remote-debugging-port=9334"]))

    def test_browser_launch_with_a_file_path_arg_is_a_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(
            [_CHROME, "/home/aayur/page.html"]))

    def test_browser_launch_with_a_windows_path_arg_is_a_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(
            ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
             "C:\\Users\\aayur\\page.html"]))

    def test_browser_launch_with_a_domain_arg_is_a_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(
            [_CHROME, "example.com"]))

    def test_browser_launch_with_localhost_port_arg_is_a_browser(self):
        self.assertTrue(cdp._is_browser_cmdline(
            [_CHROME, "localhost:9334"]))

    def test_helper_with_type_renderer_is_not_a_browser(self):
        self.assertFalse(cdp._is_browser_cmdline(_CHILD_CMDLINE))

    def test_helper_with_type_gpu_process_is_not_a_browser(self):
        self.assertFalse(cdp._is_browser_cmdline(
            [_CHROME, "--type=gpu-process", "--user-data-dir=/tmp/dbg"]))

    def test_helper_with_bare_type_flag_is_not_a_browser(self):
        self.assertFalse(cdp._is_browser_cmdline(
            [_CHROME, "--type", "zygote"]))

    def test_stray_sleep_named_chrome_is_not_a_browser(self):
        self.assertFalse(cdp._is_browser_cmdline(_FAKE_CMDLINE))

    def test_bare_word_arg_is_not_a_browser(self):
        self.assertFalse(cdp._is_browser_cmdline([_CHROME, "hello"]))

    def test_no_cmdline_info_counts_as_a_browser(self):
        # A seam/mock, or psutil could not read it: absence of
        # evidence is not evidence of being a helper.
        self.assertTrue(cdp._is_browser_cmdline(None))

    def test_empty_cmdline_is_a_zombie_not_a_browser(self):
        self.assertFalse(cdp._is_browser_cmdline([]))

    def test_bare_chrome_with_no_args_counts_as_a_browser(self):
        # A process named chrome with no arguments at all: the
        # name is all there is, so it reads as one (stated in the
        # docstring as a known residual risk).
        self.assertTrue(cdp._is_browser_cmdline([_CHROME]))


try:
    import psutil as _psutil
except ImportError:  # noqa: BLE001 — sys interpreter has no psutil
    _psutil = None


@unittest.skipUnless(_psutil is not None,
                     "psutil not installed for this interpreter")
class ChromeRunningMockTests(unittest.TestCase):
    def _running(self, procs):
        with mock.patch.object(_psutil, "process_iter",
                               return_value=procs):
            return cdp._chrome_running()

    def test_browser_process_counts_as_running(self):
        self.assertTrue(self._running(
            [_fake_proc("chrome-headless-shell", _BROWSER_CMDLINE)]))

    def test_helper_processes_alone_do_not_count_as_running(self):
        # The C2-F1 orphan state: the browser is dead, its helpers
        # are still alive and still named chrome.
        self.assertFalse(self._running([
            _fake_proc("chrome-headless-shell", _CHILD_CMDLINE),
            _fake_proc("chrome-headless-shell", _CHILD_CMDLINE),
        ]))

    def test_browser_among_helpers_still_counts_as_running(self):
        self.assertTrue(self._running([
            _fake_proc("chrome-headless-shell", _CHILD_CMDLINE),
            _fake_proc("chrome-headless-shell", _BROWSER_CMDLINE),
        ]))

    def test_stray_sleep_named_chrome_does_not_count_as_running(self):
        self.assertFalse(self._running(
            [_fake_proc("chrome", _FAKE_CMDLINE)]))

    def test_zombie_with_empty_cmdline_does_not_count_as_running(self):
        self.assertFalse(self._running(
            [_fake_proc("chrome-headless-shell", [])]))

    def test_no_chrome_processes_is_not_running(self):
        self.assertFalse(self._running(
            [_fake_proc("firefox", ["/usr/bin/firefox"]),
             _fake_proc("code", ["/usr/bin/code"])]))

    def test_name_only_mock_counts_as_running(self):
        # The B7 seam shape: proc.info with only a name (no cmdline
        # key) — the chrome136 test's fixture must keep passing.
        self.assertTrue(self._running(
            [_fake_proc("chrome-headless")]))

    def test_headless_shell_names_recognised(self):
        self.assertTrue(self._running(
            [_fake_proc("chrome-headless", _BROWSER_CMDLINE)]))


class _JsonHandler(http.server.BaseHTTPRequestHandler):
    """Answers /json/version and /json/list per the class flags."""
    version_json = True
    list_json = True
    json_body = True

    def log_message(self, *args):  # noqa: D102 — keep the suite quiet
        pass

    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler name
        if self.path == "/json/version" and type(self).version_json:
            body = b'{"Browser": "HeadlessChrome/136.0"}' \
                if type(self).json_body else b"not json"
            self._send(200, body)
        elif self.path == "/json/list" and type(self).list_json:
            body = b'[{"id": "1", "type": "page"}]' \
                if type(self).json_body else b"not json"
            self._send(200, body)
        else:
            self._send(404, b"{}")

    def _send(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _start_server(handler_cls):
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


class CdpPortAnswersTests(unittest.TestCase):
    def test_port_answering_json_version_answers(self):
        server, port = _start_server(_JsonHandler)
        try:
            self.assertTrue(cdp._cdp_port_answers(port))
        finally:
            server.shutdown()
            server.server_close()

    def test_port_answering_only_json_list_answers(self):
        # A toggle-mode server 404s /json/version — the list is
        # the fallback, and it must be enough.
        class ListOnly(_JsonHandler):
            version_json = False
            list_json = True

        server, port = _start_server(ListOnly)
        try:
            self.assertTrue(cdp._cdp_port_answers(port))
        finally:
            server.shutdown()
            server.server_close()

    def test_closed_port_does_not_answer(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            free = sock.getsockname()[1]
        self.assertFalse(cdp._cdp_port_answers(free))

    def test_server_404ing_both_paths_does_not_answer(self):
        class Nothing(_JsonHandler):
            version_json = False
            list_json = False

        server, port = _start_server(Nothing)
        try:
            self.assertFalse(cdp._cdp_port_answers(port))
        finally:
            server.shutdown()
            server.server_close()

    def test_server_answering_non_json_does_not_answer(self):
        class NonJson(_JsonHandler):
            json_body = False

        server, port = _start_server(NonJson)
        try:
            self.assertFalse(cdp._cdp_port_answers(port))
        finally:
            server.shutdown()
            server.server_close()


_EXC = Exception(
    "BrowserType.connect_over_cdp: connect ECONNREFUSED ::1:9334")


class AttachFailureBranchTests(unittest.TestCase):
    def _diagnosis(self, *, port_answers, running):
        patches = [
            mock.patch.object(bs, "_cdp_port_answers",
                              return_value=port_answers),
            mock.patch.object(bs, "_chrome_running",
                              return_value=running),
            mock.patch.object(bs, "_read_devtools_active_port",
                              return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return bs._attach_failure(9334, "http://localhost:9334", _EXC)

    def test_port_answering_says_listening_not_the_wall(self):
        result = self._diagnosis(port_answers=True, running=True)
        self.assertIn("Couldn't attach to Chrome at "
                      "http://localhost:9334", result)
        self.assertIn("answering on port 9334", result)
        self.assertIn("attach itself failed", result)
        self.assertNotIn("Chrome 136 wall", result)
        self.assertNotIn("isn't running", result)

    def test_port_answering_wins_over_the_process_scan(self):
        # Probe-first ordering: even with a browser process seen,
        # an answering port reads "listening", never the wall.
        result = self._diagnosis(port_answers=True, running=True)
        self.assertIn("answering on port 9334", result)
        self.assertNotIn("Chrome is running, and this is most likely",
                         result)

    def test_wall_when_a_browser_runs_with_no_port(self):
        # The genuine Chrome 136 wall: a real browser is running
        # and nothing answers on the port.
        result = self._diagnosis(port_answers=False, running=True)
        self.assertIn("Chrome is running", result)
        self.assertIn("Chrome 136", result)
        self.assertIn("debug profile", result)

    def test_dead_browser_names_the_dead_state_and_the_port(self):
        # The C2-F1 case, as the fixed code reaches it: the port
        # does not answer and no browser process is running.
        result = self._diagnosis(port_answers=False, running=False)
        self.assertIn("Couldn't attach to Chrome at "
                      "http://localhost:9334", result)
        self.assertIn("Chrome isn't running", result)
        self.assertIn("nothing is answering on port 9334", result)
        self.assertIn("no Chrome browser process is running", result)
        self.assertIn("may have closed or crashed", result)
        self.assertIn("user-data-dir", result)
        self.assertNotIn("Chrome is running", result)

    def test_unknown_state_says_it_cannot_tell(self):
        result = self._diagnosis(port_answers=False, running=None)
        self.assertIn("can't tell whether Chrome is running", result)
        self.assertNotIn("Chrome is running, and this is most likely",
                         result)

    def test_base_names_the_exception_in_every_branch(self):
        for port_answers, running in ((True, True), (False, True),
                                      (False, False), (False, None)):
            result = self._diagnosis(port_answers=port_answers,
                                     running=running)
            self.assertIn("connect ECONNREFUSED", result)
