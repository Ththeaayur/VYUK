"""GAPS-3 — Chrome 136 profile wall (BLOCKS #4, 2026-11-12).

WHAT CHANGED (primary source — Will Harris, Chrome for Developers,
"Changes to remote debugging switches to improve security",
17 March 2025): from Chrome 136, --remote-debugging-port and
--remote-debugging-pipe "will no longer be respected if attempting
to debug the default Chrome data directory" and "must now be
accompanied by the --user-data-dir switch to point to a
non-standard directory", because infostealers were using the port
to pull cookies out of real profiles. The failure is SILENT — the
flag is ignored, no port opens, no warning.

THE HOLE (proved on the backup before this batch): the launch
path built [chrome, --remote-debugging-port=N] with no
--user-data-dir, and the attach failure blamed a missing flag —
false on Chrome 136+ even when the flag WAS used. Zero "Chrome
136" mentions in core/, zero profile-lock / DevToolsActivePort
handling in core/desktop/.

THE HANDLING (pinned here, on fake profile dirs — the lab has no
live Chrome, so the end-to-end launch/attach against real Chrome
136 is a FIELD check, owed, not claimed):
* the launch command is built in exactly one place
  (browser_cdp._launch_argv) and ALWAYS carries a non-default
  --user-data-dir (browser_cdp._debug_profile_dir);
* the dir is diagnosed BEFORE any launch
  (browser_cdp._diagnose_profile_dir): the default profile dir,
  a live profile lock (SingletonLock + a running Chrome), a file
  where a folder should be, and an unwritable dir are each
  refused with the named cause — never a silent no-port launch;
  a stale lock (lock file, no Chrome running) proceeds with a
  note, and an unjudgeable lock fails closed;
* the attach failure names the Chrome 136 wall when Chrome is
  running, and names the DevToolsActivePort hint when the running
  Chrome opened a debugging server on another port;
* the enable flow reads DevToolsActivePort as a HINT for a port
  the running Chrome opened itself (newer Chrome's
  chrome://inspect toggle) — a hint only, the probe decides;
* every launch/restart message states the honest cost: the debug
  profile is a SEPARATE profile — the everyday profile's logins,
  extensions and open tabs are not in it.
* the restart flow diagnoses the profile BEFORE closing Chrome
  (closing the user's Chrome and then discovering the profile is
  unusable would leave them with no browser at all); a live lock
  is allowed through the early check because the close ends the
  lock holder, and the launch step re-diagnoses after the close.
* registry stays 752 (no new tool — this is handling, not a tool). GAPS-7 connectors pack (2026-11-13): 751 -> 752 — sync_updates added. SOCIAL Phase 1 (2026-11-13): 752 -> 753 — social_send added. GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754 — scheduled_agents added.
"""

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import browser_cdp as cdp
from core.desktop import browser_enable as be
from core.desktop import browser_session as bs

_CHROME_HS = {"version": {"Browser": "Chrome/139.0.0.0",
                          "Protocol-Version": "1.3"},
              "targets": []}
_STATUS = ("Connected to your Chrome (CDP attach). 3 tab(s) open. "
           "Active tab: 'Example' — https://example.com.")
_OK_DIAG = {"state": "ok", "dir": "/fake/vyuk-debug-profile",
            "message": "", "note": ""}


class LaunchArgvTests(unittest.TestCase):
    def test_argv_carries_port_and_non_default_user_data_dir(self):
        argv = cdp._launch_argv("/usr/bin/google-chrome", 9222,
                                "/fake/vyuk-debug-profile")
        self.assertEqual(argv, ["/usr/bin/google-chrome",
                                "--remote-debugging-port=9222",
                                "--user-data-dir=/fake/vyuk-debug-profile"])

    def test_argv_is_the_only_launch_command_builder(self):
        import inspect
        src = inspect.getsource(be._launch_chrome)
        self.assertIn("_launch_argv", src)
        self.assertNotIn("--remote-debugging-port=", src)

    def test_debug_profile_dir_is_never_the_default_profile(self):
        with mock.patch.object(cdp, "_home_dir",
                               return_value=pathlib.Path("/fake/home")):
            self.assertFalse(cdp._is_default_profile_dir(
                cdp._debug_profile_dir()))

    def test_debug_profile_dir_legacy_alias_ignored(self):
        # GAPS-11 removed the legacy VYUK_BROWSER_DEBUG_PROFILE_DIR
        # fallback: it was dead in production (the config field is
        # always non-None, so the leg only ever fired under a
        # stubbed config), and the canonical
        # BROWSER_DEBUG_PROFILE_DIR already controls the field. A
        # stubbed config plus the legacy var now reads as the
        # default dir, not the legacy value.
        import os
        with mock.patch.object(cdp, "_config_value",
                               return_value=None), \
                mock.patch.dict(os.environ,
                                {"VYUK_BROWSER_DEBUG_PROFILE_DIR":
                                 "/custom/dbg"}), \
                mock.patch.object(cdp, "_home_dir",
                                  return_value=pathlib.Path("/fake/home")):
            self.assertEqual(
                cdp._debug_profile_dir(),
                pathlib.Path("/fake/home/.local/share/vyuk/chrome-debug-profile"))

    def test_debug_profile_dir_empty_config_means_default(self):
        with mock.patch.object(cdp, "_config_value",
                               return_value=""), \
                mock.patch.object(cdp, "_home_dir",
                                  return_value=pathlib.Path("/fake/home")):
            self.assertEqual(
                cdp._debug_profile_dir(),
                pathlib.Path("/fake/home/.local/share/vyuk/chrome-debug-profile"))

    def test_debug_profile_dir_config_override(self):
        with mock.patch.object(cdp, "_config_value",
                               return_value="/cfg/dbg"):
            self.assertEqual(cdp._debug_profile_dir(),
                             pathlib.Path("/cfg/dbg"))

    def test_default_chrome_profile_dir_per_platform(self):
        with mock.patch.object(cdp, "_home_dir",
                               return_value=pathlib.Path("/fake/home")):
            with mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.dict("os.environ", {"LOCALAPPDATA":
                                                   "C:\\Users\\x\\AppData\\Local"}):
                self.assertEqual(
                    cdp._default_chrome_profile_dir().parts[-3:],
                    ("Google", "Chrome", "User Data"))
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(cdp._default_chrome_profile_dir(),
                                 pathlib.Path("/fake/home/Library/Application Support/Google/Chrome"))
            with mock.patch.object(sys, "platform", "linux"):
                self.assertEqual(cdp._default_chrome_profile_dir(),
                                 pathlib.Path("/fake/home/.config/google-chrome"))

    def test_is_default_profile_dir_matches_and_rejects(self):
        default = pathlib.Path("/fake/home/.config/google-chrome")
        with mock.patch.object(cdp, "_default_chrome_profile_dir",
                               return_value=default):
            self.assertTrue(cdp._is_default_profile_dir(
                "/fake/home/.config/google-chrome"))
            self.assertFalse(cdp._is_default_profile_dir(
                "/fake/home/.config/google-chrome-2"))
            self.assertFalse(cdp._is_default_profile_dir(None))


class DiagnoseProfileDirTests(unittest.TestCase):
    def test_fresh_dir_is_ok_and_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "dbg"
            self.assertFalse(target.exists())
            diag = cdp._diagnose_profile_dir(target, False)
            self.assertEqual(diag["state"], "ok")
            self.assertTrue(target.is_dir())

    def test_existing_dir_no_lock_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            diag = cdp._diagnose_profile_dir(tmp, False)
            self.assertEqual(diag["state"], "ok")
            self.assertEqual(diag["note"], "")

    def test_live_lock_is_locked_and_names_the_cause(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "SingletonLock").write_text("1234")
            diag = cdp._diagnose_profile_dir(tmp, True)
            self.assertEqual(diag["state"], "locked")
            self.assertIn("SingletonLock", diag["message"])
            self.assertIn("already running", diag["message"])

    def test_stale_lock_proceeds_with_a_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "SingletonLock").write_text("1234")
            diag = cdp._diagnose_profile_dir(tmp, False)
            self.assertEqual(diag["state"], "ok")
            self.assertIn("leftover", diag["note"])

    def test_unjudgeable_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "SingletonLock").write_text("1234")
            diag = cdp._diagnose_profile_dir(tmp, None)
            self.assertEqual(diag["state"], "unknown")
            self.assertIn("can't tell", diag["message"])

    def test_default_profile_dir_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                    cdp, "_default_chrome_profile_dir",
                    return_value=pathlib.Path(tmp)):
                diag = cdp._diagnose_profile_dir(tmp, False)
            self.assertEqual(diag["state"], "default_profile")
            self.assertIn("Chrome 136", diag["message"])
            self.assertIn("default profile", diag["message"])

    def test_file_where_a_folder_should_be(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "afile"
            target.write_text("x")
            diag = cdp._diagnose_profile_dir(target, False)
            self.assertEqual(diag["state"], "not_a_directory")
            self.assertIn("file, not a folder", diag["message"])

    def test_uncreatable_dir_is_unwritable(self):
        with tempfile.TemporaryDirectory() as tmp:
            blocker = pathlib.Path(tmp) / "afile"
            blocker.write_text("x")
            diag = cdp._diagnose_profile_dir(blocker / "sub", False)
            self.assertEqual(diag["state"], "unwritable")

    def test_missing_path_is_missing(self):
        self.assertEqual(cdp._diagnose_profile_dir(None)["state"],
                         "missing")
        self.assertEqual(cdp._diagnose_profile_dir("")["state"],
                         "missing")

    def test_diagnosis_never_raises(self):
        for path in (None, "", "/nonexistent-root-xyz/sub",
                     "\x00bad"):
            try:
                cdp._diagnose_profile_dir(path, None)
            except Exception as exc:  # noqa: BLE001 — the point
                self.fail("diagnosis raised on %r: %s" % (path, exc))


class DevToolsActivePortTests(unittest.TestCase):
    def _write(self, tmp, text):
        (pathlib.Path(tmp) / "DevToolsActivePort").write_text(text)

    def test_two_line_file_parses_the_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "63475\n/devtools/browser/0b6c1c1e\n")
            self.assertEqual(cdp._read_devtools_active_port(tmp), 63475)

    def test_host_port_form_parses(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "127.0.0.1:9222\n")
            self.assertEqual(cdp._read_devtools_active_port(tmp), 9222)

    def test_missing_file_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(cdp._read_devtools_active_port(tmp))

    def test_garbage_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, "not-a-port\n")
            self.assertIsNone(cdp._read_devtools_active_port(tmp))
            self._write(tmp, "99\n")
            self.assertIsNone(cdp._read_devtools_active_port(tmp))
            self._write(tmp, "")
            self.assertIsNone(cdp._read_devtools_active_port(tmp))

    def test_none_dir_is_none(self):
        self.assertIsNone(cdp._read_devtools_active_port(None))


class LaunchChromeTests(unittest.TestCase):
    def test_launch_chrome_argv_has_user_data_dir(self):
        captured = {}

        def fake_popen(argv, **kw):
            captured["argv"] = argv
            return mock.Mock()

        with mock.patch.object(
                be, "_debug_profile_dir",
                return_value=pathlib.Path("/fake/vyuk-debug-profile")), \
                mock.patch.object(be.subprocess, "Popen", fake_popen):
            # _launch_chrome swallows a Popen failure into False, so
            # the fake records instead of raising.
            self.assertTrue(be._launch_chrome("/usr/bin/google-chrome",
                                              9222))
        self.assertEqual(captured["argv"],
                         ["/usr/bin/google-chrome",
                          "--remote-debugging-port=9222",
                          "--user-data-dir=/fake/vyuk-debug-profile"])

    def test_checked_launch_refuses_locked_profile(self):
        locked = {"state": "locked", "dir": "/fake/dbg",
                  "message": "locked by a running Chrome", "note": ""}
        with mock.patch.object(be, "_debug_profile_dir",
                               return_value=pathlib.Path("/fake/dbg")), \
                mock.patch.object(be, "_diagnose_profile_dir",
                                  return_value=locked), \
                mock.patch.object(be, "_launch_chrome") as launch:
            ok, state, message = be._launch_chrome_checked(
                "/fake/chrome.exe", 9222)
        self.assertFalse(ok)
        self.assertEqual(state, "profile_locked")
        self.assertIn("locked", message)
        launch.assert_not_called()

    def test_checked_launch_refuses_default_profile(self):
        default = {"state": "default_profile", "dir": "/fake/default",
                   "message": "is the default profile", "note": ""}
        with mock.patch.object(be, "_debug_profile_dir",
                               return_value=pathlib.Path("/fake/default")), \
                mock.patch.object(be, "_diagnose_profile_dir",
                                  return_value=default), \
                mock.patch.object(be, "_launch_chrome") as launch:
            ok, state, _msg = be._launch_chrome_checked(
                "/fake/chrome.exe", 9222)
        self.assertFalse(ok)
        self.assertEqual(state, "profile_is_default")
        launch.assert_not_called()

    def test_checked_launch_passes_through_ok(self):
        with mock.patch.object(be, "_debug_profile_dir",
                               return_value=pathlib.Path("/fake/dbg")), \
                mock.patch.object(be, "_diagnose_profile_dir",
                                  return_value=_OK_DIAG), \
                mock.patch.object(be, "_launch_chrome",
                                  return_value=True) as launch:
            ok, state, _note = be._launch_chrome_checked(
                "/fake/chrome.exe", 9222)
        self.assertTrue(ok)
        self.assertEqual(state, "launched")
        launch.assert_called_once_with("/fake/chrome.exe", 9222)


class EnableFlowTests(unittest.TestCase):
    def _enable_env(self, *, handshake=None, running=False,
                    discovered=None, probe_side_effect=None):
        patches = [
            mock.patch.object(be, "_is_connected", return_value=False),
            mock.patch.object(be, "_set_opt_in"),
            mock.patch.object(be, "_port", return_value=9222),
            mock.patch.object(be, "_chrome_running",
                              return_value=running),
            mock.patch.object(be, "_attach", return_value="Attached."),
            mock.patch.object(be, "_status_line", return_value=_STATUS),
            mock.patch.object(be, "_read_devtools_active_port",
                              return_value=discovered),
        ]
        if probe_side_effect is not None:
            patches.append(mock.patch.object(
                be, "_probe_cdp", side_effect=probe_side_effect))
        else:
            patches.append(mock.patch.object(
                be, "_probe_cdp", return_value=handshake))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_needs_restart_names_chrome_136(self):
        self._enable_env(handshake=None, running=True)
        result = be.enable_browser_control()
        self.assertEqual(result["state"], "needs_restart")
        self.assertIn("Chrome 136", result["message"])
        self.assertIn("debug profile", result["message"])
        self.assertIn("ignores", result["message"])
        self.assertIn("tabs will come back", result["message"])

    def test_needs_launch_names_debug_profile(self):
        self._enable_env(handshake=None, running=False)
        result = be.enable_browser_control()
        self.assertEqual(result["state"], "needs_launch")
        self.assertIn("isn't running", result["message"])
        self.assertIn("debug profile", result["message"])
        self.assertIn("not in it", result["message"])

    def test_discovered_toggle_port_attaches_when_it_answers(self):
        self._enable_env(handshake=None, running=True, discovered=9333,
                         probe_side_effect=[None, _CHROME_HS])
        states = [False, True]
        be._is_connected.side_effect = lambda: states.pop(0) \
            if states else True
        result = be.enable_browser_control()
        be._attach.assert_called_once_with(9333)
        self.assertEqual(result["state"], "attached")
        self.assertIn("chrome://inspect", result["message"])
        self.assertIn("real profile", result["message"])

    def test_discovered_toggle_port_silent_is_just_a_hint(self):
        # The file names a port, nothing answers there: the enable
        # flow falls through to the honest needs_restart, it never
        # claims the hint as a session.
        self._enable_env(handshake=None, running=True, discovered=9333,
                         probe_side_effect=[None, None])
        result = be.enable_browser_control()
        self.assertEqual(result["state"], "needs_restart")
        be._attach.assert_not_called()

    def test_discovered_port_equal_to_configured_is_not_reprobed(self):
        self._enable_env(handshake=None, running=False, discovered=9222)
        result = be.enable_browser_control()
        self.assertEqual(result["state"], "needs_launch")
        be._probe_cdp.assert_called_once_with(9222)


class LaunchRestartRefusalTests(unittest.TestCase):
    def _launch_env(self, *, running=False, exe="/fake/chrome.exe",
                    diag=None):
        patches = [
            mock.patch.object(be, "_set_opt_in"),
            mock.patch.object(be, "_port", return_value=9222),
            mock.patch.object(be, "_chrome_running", return_value=running),
            mock.patch.object(be, "_find_chrome_exe",
                              return_value=(pathlib.Path(exe)
                                            if exe else None)),
            mock.patch.object(be, "_launch_chrome", return_value=True),
            mock.patch.object(be, "_wait_for_handshake",
                              return_value=True),
            mock.patch.object(be, "_attach", return_value="Attached."),
            mock.patch.object(be, "_status_line", return_value=_STATUS),
            mock.patch.object(be, "_is_connected", return_value=True),
            mock.patch.object(be, "_debug_profile_dir",
                              return_value=pathlib.Path("/fake/dbg")),
            mock.patch.object(be, "_diagnose_profile_dir",
                              return_value=diag if diag is not None
                              else _OK_DIAG),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_launch_locked_profile_refuses_and_launches_nothing(self):
        locked = {"state": "locked", "dir": "/fake/dbg",
                  "message": "locked by a running Chrome", "note": ""}
        self._launch_env(running=False, diag=locked)
        result = be.launch_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "profile_locked")
        self.assertIn("locked", result["message"])
        be._launch_chrome.assert_not_called()

    def test_launch_default_profile_refuses(self):
        default = {"state": "default_profile", "dir": "/fake/default",
                   "message": "is the default profile", "note": ""}
        self._launch_env(running=False, diag=default)
        result = be.launch_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "profile_is_default")
        be._launch_chrome.assert_not_called()

    def test_launch_success_note_names_debug_profile(self):
        self._launch_env(running=False)
        result = be.launch_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "attached")
        self.assertIn("separate debug profile", result["message"])
        self.assertIn("NOT in it", result["message"])

    def test_restart_default_profile_refuses_before_closing(self):
        # The safety case: Chrome must NOT be closed when the
        # profile is unusable — the refusal lands first.
        default = {"state": "default_profile", "dir": "/fake/default",
                   "message": "is the default profile", "note": ""}
        self._launch_env(running=True, diag=default)
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(be, "_close_chrome") as close:
            result = be.restart_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "profile_is_default")
        close.assert_not_called()
        be._launch_chrome.assert_not_called()

    def test_restart_locked_profile_closes_then_relaunches(self):
        # A live lock is allowed through the early check — the
        # close ends the lock holder — and the launch step
        # re-diagnoses after the close.
        locked = {"state": "locked", "dir": "/fake/dbg",
                  "message": "locked by a running Chrome", "note": ""}
        ok_after_close = {"state": "ok", "dir": "/fake/dbg",
                          "message": "", "note": ""}
        self._launch_env(running=True, diag=locked)
        be._diagnose_profile_dir.side_effect = [locked, ok_after_close]
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(be, "_close_chrome",
                                  return_value=True) as close:
            result = be.restart_chrome_for_browser_control(confirm=True)
        close.assert_called_once_with()
        be._launch_chrome.assert_called_once()
        self.assertEqual(result["state"], "attached")


class AttachFailureTests(unittest.TestCase):
    class _FakeChromium:
        def connect_over_cdp(self, url, timeout=None):
            raise RuntimeError("connection refused")

    class _FakePw:
        def __init__(self, test):
            self.chromium = test._FakeChromium()

    def _attach_env(self, running):
        patches = [
            mock.patch.object(bs, "_real_chrome_attach_allowed",
                              return_value=True),
            mock.patch.object(bs, "_chrome_running",
                              return_value=running),
            # C2-F1: _attach_failure probes the CDP port first, so
            # the probe is held at False here — these tests pin the
            # _chrome_running branches given a non-answering port.
            # (Without the pin, a real Chrome on the same port from
            # a parallel run could flip a branch mid-test.)
            mock.patch.object(bs, "_cdp_port_answers",
                              return_value=False),
            mock.patch.object(bs, "_start_pw",
                              return_value=self._FakePw(self)),
            mock.patch.object(bs, "_read_devtools_active_port",
                              return_value=None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        bs._state["pw"] = None
        bs._state["context"] = None
        self.addCleanup(lambda: bs._state.update(pw=None, context=None,
                                                 browser=None, page=None,
                                                 mode=None))

    def test_attach_failure_names_chrome_136_when_running(self):
        self._attach_env(running=True)
        result = bs._impl_attach_cdp()
        self.assertIn("Couldn't attach to Chrome at", result)
        self.assertIn("Chrome 136", result)
        self.assertIn("debug profile", result)
        self.assertNotIn("only opens that port when it is started",
                         result)

    def test_attach_failure_names_user_data_dir_when_not_running(self):
        self._attach_env(running=False)
        result = bs._impl_attach_cdp()
        self.assertIn("Chrome isn't running", result)
        self.assertIn("user-data-dir", result)

    def test_attach_failure_names_discovered_port_hint(self):
        self._attach_env(running=True)
        bs._read_devtools_active_port.return_value = 9333
        result = bs._impl_attach_cdp()
        self.assertIn("9333", result)
        self.assertIn("DevToolsActivePort", result)

    def test_attach_uses_the_given_port(self):
        self._attach_env(running=True)
        result = bs._impl_attach_cdp(9333)
        self.assertIn("localhost:9333", result)


class SourcePinTests(unittest.TestCase):
    def test_chrome_136_named_in_the_browser_code(self):
        for rel in ("core/desktop/browser_cdp.py",
                    "core/desktop/browser_enable.py",
                    "core/desktop/browser_session.py",
                    "core/browser/browser.py",  # moved from core/browser.py by the click-anywhere Batch 1 shadowing fix (core/browser/ package)
                    "core/config.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIn("Chrome 136", src, rel)

    def test_primary_source_cited(self):
        src = (ROOT / "core" / "desktop" / "browser_cdp.py") \
            .read_text(encoding="utf-8")
        self.assertIn(
            "https://developer.chrome.com/blog/remote-debugging-port",
            src)

    def test_config_field_exists(self):
        from core import config as cfg
        self.assertTrue(hasattr(cfg.config, "browser_debug_profile_dir"))
        self.assertIsInstance(cfg.config.browser_debug_profile_dir, str)

    def test_guided_error_and_status_name_the_wall(self):
        self.assertIn("Chrome 136", bs._guided_no_session("boom"))
        self.assertIn("Chrome 136", bs._impl_status())

    def test_registry_unchanged_no_new_tool(self):
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 772)  # INTENTIONAL — GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169; Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        names = {d.get("name") for d in decls}
        self.assertNotIn("chrome136", names)
        self.assertNotIn("browser_debug_profile", names)


try:
    import psutil as _psutil
except ImportError:  # noqa: BLE001 — sys interpreter has no psutil
    _psutil = None


class ChromeRunningProcNamesTests(unittest.TestCase):
    """Field check B7 (2026-11-17): _chrome_running missed the
    headless shell entirely — its process name is
    "chrome-headless-shell" (psutil's comm truncates it to
    "chrome-headless"), so the enable flow reported needs_launch
    instead of needs_restart against a running headless Chrome."""

    def test_headless_shell_names_are_in_the_chrome_proc_set(self):
        self.assertIn("chrome-headless-shell", cdp._CHROME_PROC_NAMES)
        self.assertIn("chrome-headless", cdp._CHROME_PROC_NAMES)

    @unittest.skipUnless(_psutil is not None,
                         "psutil not installed for this interpreter")
    def test_chrome_running_recognises_headless_shell(self):
        def fake_procs(names):
            procs = []
            for name in names:
                proc = mock.Mock()
                proc.info = {"name": name}
                procs.append(proc)
            return procs

        with mock.patch.object(_psutil, "process_iter",
                               return_value=fake_procs(
                                   ["chrome-headless"])):
            self.assertTrue(cdp._chrome_running())
        with mock.patch.object(_psutil, "process_iter",
                               return_value=fake_procs(
                                   ["chrome-headless-shell"])):
            self.assertTrue(cdp._chrome_running())
        with mock.patch.object(_psutil, "process_iter",
                               return_value=fake_procs(
                                   ["firefox", "code"])):
            self.assertFalse(cdp._chrome_running())


if __name__ == "__main__":
    unittest.main()
