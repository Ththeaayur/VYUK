"""GAPS-9 suite: the companion server routes through the funnel.

The phone is another caller of the funnel, not a second front door:
every phone action that has a funnel tool runs through
ToolRegistry.execute (same confirm gate, money guard, ledger,
receipt); the two server-side actions (screen, files) leave an
activity-log line each; /api/status is server-state introspection.

SAFETY: the power and lock tests run ONLY on a tree where the
funnel routing is present (module-level _FUNNEL_ROUTED). On the
backup the routing is absent and driving /api/power would call
the raw power_control — a real shutdown. Those tests SKIP there;
the other live tests FAIL there, which is the expected backup
shape (the new suite does not exist in the backup).
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..")))

_COMPANION_SRC = open(os.path.join(
    os.path.dirname(__file__), "..", "core", "companion.py"),
    encoding="utf-8").read()
_FUNNEL_ROUTED = '_funnel_call("power_control"' in _COMPANION_SRC


class _FakeHeaders(dict):
    def get(self, key, default=None):  # noqa: D102
        return super().get(key, default)


def _drive(session, token, handler_name, body=None, query="/api/x"):
    """Drive one real handler method with a fake HTTP envelope."""
    from core import companion as _comp

    raw = json.dumps(body or {}).encode() if body is not None else b""
    h = _comp._CompanionHandler.__new__(_comp._CompanionHandler)
    h.client_address = ("127.0.0.1", 54321)
    h.server = type("S", (), {"session": session})()
    h.headers = _FakeHeaders({
        "Content-Length": str(len(raw)),
        "Cookie": f"vyuk-companion={token}",
    })
    h.path = query
    h.rfile = io.BytesIO(raw)
    h.wfile = io.BytesIO()
    h.request_version = "HTTP/1.1"
    h.close_connection = True
    sent = {"status": None, "headers": {}}

    def _resp(code, _msg=None):
        sent["status"] = code

    def _hdr(key, value):
        sent["headers"][key] = value

    h.send_response = _resp  # type: ignore[method-assign]
    h.send_header = _hdr  # type: ignore[method-assign]
    h.end_headers = lambda: None  # type: ignore[method-assign]
    getattr(h, handler_name)()
    return sent["status"], h.wfile.getvalue().decode("utf-8", "replace")


def _activity():
    path = os.path.join(os.path.dirname(__file__), "..",
                        "data", "activity.log")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class CompanionFunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core import companion as _comp
        from core.funnel.tools_registry import tools as _tools

        cls._comp = _comp
        cls._tools = _tools
        cls.fixture = tempfile.mkdtemp(prefix="gaps9-files-")
        with open(os.path.join(cls.fixture, "hello.txt"), "w",
                  encoding="utf-8") as f:
            f.write("hello from the fixture\n")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture, ignore_errors=True)

    def setUp(self):
        self.session = self._comp.get_session()
        ok, msg = self.session.start(port=0, files_root=self.fixture)
        if not ok:
            self.skipTest(f"companion would not start: {msg}")
        self.token = self.session.issue_token("127.0.0.1")
        self.calls = []
        self._real_execute = self._tools.execute

        def _spy(name, args, *a, **k):
            self.calls.append((name, dict(args)))
            return self._real_execute(name, args, *a, **k)

        self._tools.execute = _spy  # type: ignore[method-assign]
        self._activity_before = _activity()

    def tearDown(self):
        self._tools.execute = self._real_execute  # type: ignore[method-assign]
        try:
            self.session.stop()
        except Exception:  # noqa: BLE001 — stop must never fail a test
            pass

    def _new_activity(self):
        return _activity()[len(self._activity_before):]

    # -- static: the bypass is gone -------------------------------------
    def test_no_direct_calls(self):
        for needle in [
            "_ci.mouse_control(", "_ci.type_text(", "_ci.press_hotkey(",
            "_cs.media_control(", "_vol.set_volume_level(",
            "_cs.write_clipboard(", "pyperclip.paste(",
            "power_control(action, confirm=True)",
            "def _lock_workstation",
        ]:
            self.assertNotIn(needle, _COMPANION_SRC, needle)
        for tool in ["mouse_control", "type_text", "press_hotkey",
                     "media_control", "set_volume_level", "read_clipboard",
                     "write_clipboard", "lock_screen", "power_control"]:
            self.assertIn(f'"{tool}"', _COMPANION_SRC, tool)

    def test_census_unchanged(self):
        decls = self._tools.get_declarations()
        self.assertEqual(len(decls), 756)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(len({d["name"] for d in decls}), 756)  # INTENTIONAL — Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(
            sum(len(d["parameters"]["properties"]) for d in decls), 2078)  # INTENTIONAL — GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine

    # -- live: free actions drive the funnel -----------------------------
    def test_type_routes_through_funnel(self):
        status, _ = _drive(self.session, self.token, "_post_type",
                           {"text": "gaps9-proof"})
        self.assertEqual(status, 200)
        self.assertIn(("type_text", {"text": "gaps9-proof"}), self.calls)
        self.assertIn("type_text", self._new_activity())

    def test_key_routes_through_funnel(self):
        status, _ = _drive(self.session, self.token, "_post_key",
                           {"keys": "ctrl+c"})
        self.assertEqual(status, 200)
        self.assertIn(("press_hotkey", {"keys": "ctrl+c"}), self.calls)
        self.assertIn("press_hotkey", self._new_activity())

    def test_media_routes_through_funnel(self):
        status, _ = _drive(self.session, self.token, "_post_media",
                           {"action": "play_pause"})
        self.assertEqual(status, 200)
        self.assertIn(("media_control", {"action": "play_pause"}),
                      self.calls)
        self.assertIn("media_control", self._new_activity())
        self.calls.clear()
        status, _ = _drive(self.session, self.token, "_post_media",
                           {"percent": 50})
        self.assertEqual(status, 200)
        self.assertIn(("set_volume_level", {"percent": 50}), self.calls)

    def test_clipboard_write_routes_through_funnel(self):
        status, _ = _drive(self.session, self.token, "_post_clipboard",
                           {"text": "gaps9"})
        self.assertEqual(status, 200)
        self.assertIn(("write_clipboard", {"text": "gaps9"}), self.calls)
        self.assertIn("write_clipboard", self._new_activity())

    def test_clipboard_read_routes_through_funnel(self):
        status, payload = _drive(self.session, self.token, "_get_clipboard")
        self.assertEqual(status, 200)
        self.assertIn(("read_clipboard", {}), self.calls)
        self.assertIn("read_clipboard", self._new_activity())
        self.assertIn('"text":', payload)

    def test_touch_scroll_routes_through_funnel(self):
        status, _ = _drive(self.session, self.token, "_post_touch",
                           {"action": "scroll", "amount": 3})
        self.assertEqual(status, 200)
        self.assertIn(("mouse_control",
                       {"action": "scroll", "amount": 3}), self.calls)
        self.assertIn("mouse_control", self._new_activity())

    # -- live: confirm-gated actions fail closed, then run when approved --
    def test_shutdown_without_phone_confirm_refused_pre_funnel(self):
        if not _FUNNEL_ROUTED:
            self.skipTest("no funnel routing on this tree")
        status, payload = _drive(self.session, self.token, "_post_power",
                                 {"action": "shutdown"})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])
        self.assertIn("tap it again", payload)

    def test_shutdown_fail_closed_without_desktop_handler(self):
        if not _FUNNEL_ROUTED:
            self.skipTest("no funnel routing on this tree")
        status, payload = _drive(
            self.session, self.token, "_post_power",
            {"action": "shutdown", "confirm": True})
        self.assertEqual(status, 400)
        self.assertIn(("power_control",
                       {"action": "shutdown", "confirm": True}), self.calls)
        self.assertIn("needs the user's OK", payload)
        self.assertIn("not confirmed", self._new_activity())

    def test_approved_shutdown_dispatches_with_stubbed_leaf(self):
        if not _FUNNEL_ROUTED:
            self.skipTest("no funnel routing on this tree")
        from core import confirm_gate as _cg

        saw = []
        orig = self._tools._tools["power_control"]["handler"]
        self._tools._tools["power_control"]["handler"] = (
            lambda action, confirm=False: saw.append(
                (action, confirm)) or "stubbed: shutdown scheduled")
        try:
            _cg.register_handler(lambda *a, **k: True)
            try:
                status, payload = _drive(
                    self.session, self.token, "_post_power",
                    {"action": "shutdown", "confirm": True})
            finally:
                _cg.clear_handler()
        finally:
            self._tools._tools["power_control"]["handler"] = orig
        self.assertEqual(saw, [("shutdown", True)])
        self.assertEqual(status, 200)
        self.assertIn("stubbed", payload)

    def test_lock_routes_through_funnel_with_stubbed_leaf(self):
        if not _FUNNEL_ROUTED:
            self.skipTest("no funnel routing on this tree")
        saw = []
        orig = self._tools._tools["lock_screen"]["handler"]
        self._tools._tools["lock_screen"]["handler"] = (
            lambda: saw.append(True) or "stubbed: locked")
        try:
            status, payload = _drive(self.session, self.token, "_post_power",
                                     {"action": "lock"})
        finally:
            self._tools._tools["lock_screen"]["handler"] = orig
        self.assertEqual(saw, [True])
        self.assertEqual(status, 200)
        self.assertIn("stubbed", payload)

    # -- live: server-side actions leave their trail ---------------------
    def test_file_list_and_download_audited(self):
        status, payload = _drive(self.session, self.token, "_list_files",
                                 query="/api/files?path=")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [])
        self.assertIn("hello.txt", payload)
        self.assertIn("companion_files", self._new_activity())
        self.assertIn("listed", self._new_activity())
        status, payload = _drive(
            self.session, self.token, "_download_file",
            query="/api/files/download?path=hello.txt")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [])
        self.assertIn("hello from the fixture", payload)
        self.assertIn("downloaded hello.txt", self._new_activity())

    def test_screen_first_frame_logged_once(self):
        real_capture = self.session.capture_frame
        self.session.capture_frame = (
            lambda: (b"\xff\xd8\xff\xe0fake-jpeg", None))
        try:
            self.session.screen_logged = False
            status, _ = _drive(self.session, self.token, "_get_screen")
            status2, _ = _drive(self.session, self.token, "_get_screen")
        finally:
            self.session.capture_frame = real_capture
        self.assertEqual(status, 200)
        self.assertEqual(status2, 200)
        self.assertEqual(self.calls, [])
        self.assertEqual(self._new_activity().count("companion_screen"), 1)

    def test_status_calls_no_funnel_tool(self):
        status, payload = _drive(self.session, self.token, "_get_status")
        self.assertEqual(status, 200)
        self.assertEqual(self.calls, [])
        self.assertIn("running", payload)

    # -- live: the pairing gate and validation still hold ----------------
    def test_unpaired_request_refused(self):
        status, payload = _drive(self.session, "bogus-token", "_post_type",
                                 {"text": "nope"})
        self.assertEqual(status, 401)
        self.assertEqual(self.calls, [])
        self.assertIn("Not paired", payload)

    def test_unknown_touch_action_refused_pre_funnel(self):
        status, payload = _drive(self.session, self.token, "_post_touch",
                                 {"action": "teleport"})
        self.assertEqual(status, 400)
        self.assertEqual(self.calls, [])
        self.assertIn("Unknown touch action", payload)


if __name__ == "__main__":
    unittest.main()
