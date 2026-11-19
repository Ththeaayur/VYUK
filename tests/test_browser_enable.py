"""Browser-enable batch (2026-11-05): one-click enable + Chrome
relaunch with the debug port.

Owner field report 2026-11-05: "I'm not able to control my
browser." Gap proved from the tree: the ONLY path was manual —
flip the opt-in in Settings/config (or set the env var) AND start
Chrome yourself with --remote-debugging-port=9222 AND then
browser_control launch. The Settings toggle only flipped the
flag; it never relaunched Chrome, never verified the attach, and
never explained the flag. No enable helper existed anywhere.

THE ONE RULE (pinned here): the enable flow is explicit and
verified. The opt-in default stays OFF; only the Settings
"Enable browser control" button (GUI only — never a voice
action, never on a plain browser request) flips it on. Every
enable/launch/restart result is a dict {state, message} and
never raises; "attached" is claimed ONLY after a status read
verifies the session is actually connected. Chrome is closed
ONLY by the explicit restart button (confirm=True), only on
Windows, after the warning. Disabling detaches first, then flips
the flag off, and never kills Chrome or closes tabs.

This suite pins, on both interpreters (Chrome/Playwright/CDP
are all faked through the module seams — the lab has no Chrome):

* enable flips the persisted opt-in on, and a plain browser
  request never calls the enable flow (source pin: no caller in
  browser_session/browser_dispatch);
* already attached: enable reports it without flipping or
  re-attaching; attach verified by status before any success
  claim; attach failure reports the error and claims nothing;
* port answers but is not Chrome: port_in_use, attach never
  attempted; custom port (9333) is honoured everywhere — probe,
  launch and messages name 9333, never a hardcoded 9222;
* port closed + Chrome not running: needs_launch; port closed +
  Chrome running without the flag: needs_restart, with the
  honest reason (Chrome ignores the flag while an instance
  lives) and NO silent close or launch; unknown Chrome state:
  chrome_state_unknown;
* launch/restart refuse without confirm=True (nothing launched,
  nothing closed, opt-in untouched); launch with Chrome already
  running refuses as needs_restart; launch timeout and
  chrome_not_found report honestly; restart on non-Windows
  refuses as needs_manual_restart; restart on Windows closes,
  relaunches, attaches and verifies;
* disable detaches first, then flips off; already-off reports
  already_off; disable never launches or closes Chrome;
* default opt-in is OFF in code; refusal text points at the new
  one-click path and names the configured port; guided error and
  status keep the open_url boundary sentence intact; the
  browser_control description no longer promises automatic
  attach; real-Chrome-only intact (no own-browser launch);
* registry stays 655 (no new tool — enable is Settings-only);
* GUI wiring by AST pin: Enable + Chrome-action buttons and the
  status label exist in the browser card; the handlers, the
  completed slot, the toggle-off detach and the page-show
  refresh exist; the BridgeSignals entry is connected.

FIELD LEG OWED: lab fakes prove the decision/validation/wiring
logic only — Chrome relaunch with his real tabs, real attach
after relaunch, and the wrong-port / port-in-use cases on his
Windows machine are still owed.
"""

import ast
import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.desktop import browser_enable as be
from core.desktop import browser_cdp as cdp
from core.desktop import browser_session as bs

_CHROME_HS = {"version": {"Browser": "Chrome/139.0.0.0",
                          "Protocol-Version": "1.3"},
              "targets": []}
_OTHER_HS = {"version": {"Browser": "SomeProxy/1.0"},
             "targets": []}
_STATUS = ("Connected to your Chrome (CDP attach). 3 tab(s) open. "
           "Active tab: 'Example' — https://example.com.")


def _fake_enable_env(test, *, connected=False, handshake=None,
                     running=False, port=9222, attach="Attached."):
    """Patch every seam enable_browser_control touches."""
    patches = [
        mock.patch.object(be, "_is_connected", return_value=connected),
        mock.patch.object(be, "_set_opt_in"),
        mock.patch.object(be, "_port", return_value=port),
        mock.patch.object(be, "_probe_cdp", return_value=handshake),
        mock.patch.object(be, "_chrome_running", return_value=running),
        mock.patch.object(be, "_attach", return_value=attach),
        mock.patch.object(be, "_status_line", return_value=_STATUS),
        # GAPS-3: the DevToolsActivePort discovery is a seam too —
        # None by default, so the existing tests never read the
        # real default profile dir.
        mock.patch.object(be, "_read_devtools_active_port",
                          return_value=None),
    ]
    for p in patches:
        p.start()
        test.addCleanup(p.stop)
    return be


class EnableTests(unittest.TestCase):
    def test_already_attached_reports_without_flipping_or_attaching(self):
        _fake_enable_env(self, connected=True)
        with mock.patch.object(be, "_attach") as attach:
            result = be.enable_browser_control()
        self.assertEqual(result["state"], "already_attached")
        be._set_opt_in.assert_not_called()
        attach.assert_not_called()
        self.assertIn("already", result["message"].lower())

    def test_enable_flips_opt_in_then_attaches_and_verifies(self):
        _fake_enable_env(self, connected=False, handshake=_CHROME_HS)
        # _attach_and_verify re-reads _is_connected: False at
        # enable time, True after the attach. Drive it with a
        # side effect instead of a fixed return.
        states = [False, True]
        be._is_connected.side_effect = lambda: states.pop(0) \
            if states else True
        result = be.enable_browser_control()
        be._set_opt_in.assert_called_once_with(True)
        be._attach.assert_called_once_with()
        self.assertEqual(result["state"], "attached")
        self.assertIn("3 tab(s)", result["message"])

    def test_enable_attach_failure_claims_nothing(self):
        _fake_enable_env(self, connected=False, handshake=_CHROME_HS,
                         attach="Couldn't attach to Chrome at "
                         "http://localhost:9222 (timeout).")
        result = be.enable_browser_control()
        self.assertEqual(result["state"], "attach_failed")
        self.assertIn("Couldn't attach", result["message"])
        self.assertNotIn("Attached to your Chrome", result["message"])

    def test_enable_port_in_use_never_attempts_attach(self):
        _fake_enable_env(self, connected=False, handshake=_OTHER_HS)
        with mock.patch.object(be, "_attach") as attach:
            result = be.enable_browser_control()
        self.assertEqual(result["state"], "port_in_use")
        attach.assert_not_called()
        self.assertIn("not Chrome", result["message"])

    def test_enable_port_closed_chrome_not_running_needs_launch(self):
        _fake_enable_env(self, connected=False, handshake=None,
                         running=False, port=9222)
        with mock.patch.object(be, "_attach") as attach, \
                mock.patch.object(be, "_launch_chrome") as launch, \
                mock.patch.object(be, "_close_chrome") as close:
            result = be.enable_browser_control()
        self.assertEqual(result["state"], "needs_launch")
        attach.assert_not_called()
        launch.assert_not_called()
        close.assert_not_called()
        self.assertIn("isn't running", result["message"])

    def test_enable_port_closed_chrome_running_needs_restart_no_kill(self):
        _fake_enable_env(self, connected=False, handshake=None,
                         running=True)
        with mock.patch.object(be, "_attach") as attach, \
                mock.patch.object(be, "_launch_chrome") as launch, \
                mock.patch.object(be, "_close_chrome") as close:
            result = be.enable_browser_control()
        self.assertEqual(result["state"], "needs_restart")
        attach.assert_not_called()
        launch.assert_not_called()
        close.assert_not_called()
        self.assertIn("ignores", result["message"])
        self.assertIn("tabs will come back", result["message"])

    def test_enable_chrome_state_unknown_is_honest(self):
        _fake_enable_env(self, connected=False, handshake=None,
                         running=None)
        result = be.enable_browser_control()
        self.assertEqual(result["state"], "chrome_state_unknown")
        self.assertIn("can't tell", result["message"])

    def test_enable_custom_port_honoured_not_hardcoded_9222(self):
        _fake_enable_env(self, connected=False, handshake=None,
                         running=False, port=9333)
        be._probe_cdp.assert_not_called()  # called inside enable
        result = be.enable_browser_control()
        be._probe_cdp.assert_called_once_with(9333)
        self.assertEqual(result["state"], "needs_launch")
        self.assertIn("9333", result["message"])
        self.assertNotIn("9222", result["message"])

    def test_enable_never_raises(self):
        _fake_enable_env(self, connected=False, handshake=_CHROME_HS)
        be._set_opt_in.side_effect = RuntimeError("store blew up")
        result = be.enable_browser_control()
        self.assertEqual(result["state"], "failed")
        self.assertIn("Couldn't turn browser control on",
                      result["message"])


class LaunchRestartTests(unittest.TestCase):
    def _launch_env(self, *, running=False, exe="/fake/chrome.exe",
                    launched=True, handshake_ok=True, connected=True):
        patches = [
            mock.patch.object(be, "_set_opt_in"),
            mock.patch.object(be, "_port", return_value=9222),
            mock.patch.object(be, "_chrome_running", return_value=running),
            mock.patch.object(be, "_find_chrome_exe",
                              return_value=(pathlib.Path(exe)
                                            if exe else None)),
            mock.patch.object(be, "_launch_chrome",
                              return_value=launched),
            mock.patch.object(be, "_wait_for_handshake",
                              return_value=handshake_ok),
            mock.patch.object(be, "_attach", return_value="Attached."),
            mock.patch.object(be, "_status_line", return_value=_STATUS),
            mock.patch.object(be, "_is_connected",
                              return_value=connected),
            # GAPS-3: the profile diagnosis is a seam — ok by
            # default, so the existing tests never create the real
            # debug profile dir.
            mock.patch.object(be, "_debug_profile_dir",
                              return_value=pathlib.Path(
                                  "/fake/vyuk-debug-profile")),
            mock.patch.object(be, "_diagnose_profile_dir",
                              return_value={"state": "ok",
                                            "dir": "/fake/vyuk-debug-profile",
                                            "message": "", "note": ""}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_launch_without_confirm_refuses_and_touches_nothing(self):
        self._launch_env()
        result = be.launch_chrome_for_browser_control(confirm=False)
        self.assertEqual(result["state"], "needs_confirm")
        be._set_opt_in.assert_not_called()
        be._launch_chrome.assert_not_called()

    def test_launch_with_chrome_running_refuses_as_restart(self):
        self._launch_env(running=True)
        result = be.launch_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "needs_restart")
        be._launch_chrome.assert_not_called()
        be._find_chrome_exe.assert_not_called()

    def test_launch_happy_path_attaches_and_verifies(self):
        self._launch_env(running=False)
        result = be.launch_chrome_for_browser_control(confirm=True)
        be._set_opt_in.assert_called_once_with(True)
        be._launch_chrome.assert_called_once()
        args = be._launch_chrome.call_args.args
        self.assertEqual(str(args[0]), "/fake/chrome.exe")
        self.assertEqual(args[1], 9222)
        self.assertEqual(result["state"], "attached")

    def test_launch_chrome_not_found(self):
        self._launch_env(running=False, exe=None)
        result = be.launch_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "chrome_not_found")
        be._launch_chrome.assert_not_called()

    def test_launch_handshake_timeout(self):
        self._launch_env(running=False, handshake_ok=False)
        result = be.launch_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "launch_timeout")
        be._attach.assert_not_called()

    def test_restart_without_confirm_refuses_and_closes_nothing(self):
        self._launch_env(running=True)
        with mock.patch.object(be, "_close_chrome") as close:
            result = be.restart_chrome_for_browser_control(confirm=False)
        self.assertEqual(result["state"], "needs_confirm")
        close.assert_not_called()
        be._set_opt_in.assert_not_called()

    def test_restart_on_non_windows_is_honest_manual_path(self):
        self.assertFalse(sys.platform.startswith("win"))
        self._launch_env(running=True)
        result = be.restart_chrome_for_browser_control(confirm=True)
        self.assertEqual(result["state"], "needs_manual_restart")
        self.assertIn("--remote-debugging-port=9222",
                      result["message"])

    def test_restart_on_windows_closes_relaunches_verifies(self):
        self._launch_env(running=True)
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(be, "_close_chrome",
                                   return_value=True) as close:
                result = be.restart_chrome_for_browser_control(
                    confirm=True)
        close.assert_called_once_with()
        be._launch_chrome.assert_called_once()
        self.assertEqual(result["state"], "attached")

    def test_restart_close_failure_reports_and_does_not_launch(self):
        self._launch_env(running=True)
        with mock.patch.object(sys, "platform", "win32"):
            with mock.patch.object(be, "_close_chrome",
                                   return_value=False):
                result = be.restart_chrome_for_browser_control(
                    confirm=True)
        self.assertEqual(result["state"], "restart_failed")
        be._launch_chrome.assert_not_called()


class DisableTests(unittest.TestCase):
    def test_disable_detaches_first_then_flips_off(self):
        with mock.patch.object(be, "_is_connected",
                               return_value=True), \
                mock.patch.object(be, "_opt_in", return_value=True), \
                mock.patch.object(be, "_detach",
                                  return_value="Detached.") as detach, \
                mock.patch.object(be, "_set_opt_in") as set_opt, \
                mock.patch.object(be, "_launch_chrome") as launch, \
                mock.patch.object(be, "_close_chrome") as close:
            result = be.disable_browser_control()
        detach.assert_called_once_with()
        set_opt.assert_called_once_with(False)
        launch.assert_not_called()
        close.assert_not_called()
        self.assertEqual(result["state"], "disabled")
        self.assertIn("untouched", result["message"])

    def test_disable_already_off(self):
        with mock.patch.object(be, "_is_connected",
                               return_value=False), \
                mock.patch.object(be, "_opt_in", return_value=False), \
                mock.patch.object(be, "_detach") as detach, \
                mock.patch.object(be, "_set_opt_in") as set_opt:
            result = be.disable_browser_control()
        detach.assert_not_called()
        set_opt.assert_called_once_with(False)
        self.assertEqual(result["state"], "already_off")


class TextAndBoundaryTests(unittest.TestCase):
    def test_default_opt_in_is_off_in_code(self):
        # GAPS-10: the field moved to the versioned spec
        # (core/config_spec.py) with the same default (off).
        src = (ROOT / "core" / "config_spec.py").read_text(encoding="utf-8")
        self.assertIn(
            'browser_allow_real_chrome_attach: bool = field('
            'default=False, metadata={"env": '
            '"BROWSER_ALLOW_REAL_CHROME_ATTACH"', src)
        import os
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VYUK_ALLOW_REAL_CHROME_ATTACH", None)
            with mock.patch.object(cdp, "_config_value",
                                   return_value=None):
                self.assertFalse(cdp._real_chrome_attach_allowed())

    def test_refusal_points_at_enable_and_names_configured_port(self):
        text = cdp._REAL_ATTACH_REFUSAL.format(port=9333)
        self.assertIn("Enable browser control", text)
        self.assertIn("9333", text)
        self.assertNotIn("{port}", text)
        self.assertNotIn("9222", text)
        self.assertIn("OFF by default", text)

    def test_guided_error_keeps_open_url_boundary_and_enable_pointer(self):
        text = bs._guided_no_session("boom")
        self.assertIn("Enable browser control", text)
        self.assertIn(bs._OPEN_URL_BOUNDARY, text)
        self.assertIn("boom", text)
        # First-run onboarding (2026-11-16): the guided error no
        # longer quotes the configured port — it names the one-tap
        # way back instead.
        self.assertIn("Connect my browser", text)

    def test_status_points_at_enable(self):
        self.assertIn("Enable browser control", bs._impl_status())

    def test_dispatch_description_no_longer_promises_auto_attach(self):
        import inspect
        from core import browser_dispatch as bd
        src = inspect.getsource(bd.register_all)
        self.assertIn("Enable browser control", src)
        self.assertNotIn("attach to your Chrome automatically", src)
        self.assertIn("open_url window", src)

    def test_no_plain_browser_request_calls_enable(self):
        # The enable flow is Settings-only: no caller in the
        # session/bootstrap/dispatch paths.
        for path in ("core/desktop/browser_session.py",
                     "core/browser_dispatch.py",
                     "core/desktop/browser_cdp.py"):
            src = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("enable_browser_control", src)
            self.assertNotIn("launch_chrome_for_browser_control", src)
            self.assertNotIn("restart_chrome_for_browser_control", src)

    def test_real_chrome_only_no_own_browser_launch(self):
        # Pin on CODE (an import or a launch call), not on prose:
        # the module docstring itself discusses Playwright, so a
        # bare-word ban would fail on its own documentation.
        src = (ROOT / "core" / "desktop" / "browser_enable.py") \
            .read_text(encoding="utf-8")
        self.assertNotIn("chromium.launch", src)
        self.assertNotIn("from playwright", src)
        self.assertNotIn("import playwright", src)
        self.assertNotIn("sync_playwright", src)
        session_src = (ROOT / "core" / "desktop" / "browser_session.py") \
            .read_text(encoding="utf-8")
        self.assertIn("connect_over_cdp", session_src)
        self.assertNotIn("chromium.launch", session_src)

    def test_registry_unchanged_no_new_tool(self):
        from core.funnel.tools_registry import tools
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 758)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        names = {d.get("name") for d in decls}
        self.assertNotIn("browser_enable", names)
        self.assertNotIn("enable_browser_control", names)


class GuiWiringTests(unittest.TestCase):
    def _src(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_browser_card_has_enable_buttons_and_status(self):
        src = self._src("gui/window_settings_groups.py")
        self.assertIn("self.btn_browser_enable", src)
        self.assertIn('"Enable browser control"', src)
        self.assertIn("self.btn_browser_chrome_action", src)
        self.assertIn("self.browser_enable_status_label", src)
        self.assertIn("self._refresh_browser_enable_status()", src)
        self.assertIn("_on_browser_enable_clicked", src)
        self.assertIn("_on_browser_chrome_action_clicked", src)

    def test_brain_services_handlers_and_toggle_off_detach(self):
        src = self._src("gui/window_brain_services.py")
        for name in ("_on_browser_enable_clicked",
                     "_on_browser_chrome_action_clicked",
                     "_on_browser_enable_completed",
                     "_refresh_browser_enable_status"):
            self.assertIn(f"def {name}", src)
        self.assertIn("disable_browser_control", src)
        self.assertIn("confirm=True", src)
        self.assertIn("browser_enable_completed", src)

    def test_signal_exists_and_is_connected(self):
        shell_src = self._src("gui/window_widgets_shell.py")
        self.assertIn("browser_enable_completed = Signal(str)",
                      shell_src)
        frame_src = self._src("gui/window_shell_frame.py")
        self.assertIn("browser_enable_completed.connect(", frame_src)
        self.assertIn("_on_browser_enable_completed", frame_src)

    def test_page_show_refreshes_browser_status(self):
        src = self._src("gui/window_chat_nav.py")
        self.assertIn("self._refresh_browser_enable_status()", src)


class AttachThreadTests(unittest.TestCase):
    """Field fix #1 (2026-11-17, B4): _attach called
    _impl_attach_cdp directly on the caller's thread, so the
    Playwright objects were created there — and every later
    worker-thread call failed "Cannot switch to a different
    thread" while the attach-time verification (same wrong
    thread) still read fine and reported success. The attach
    and the status read must both run on the browser worker
    thread, through _submit."""

    def test_attach_runs_on_the_browser_worker_thread(self):
        with mock.patch.object(bs, "_submit",
                               return_value="Attached.") as submit, \
                mock.patch.object(bs, "_impl_attach_cdp") as impl:
            self.assertEqual(be._attach(), "Attached.")
            submit.assert_called_once_with(bs._impl_attach_cdp,
                                           kind="launch")
            impl.assert_not_called()

    def test_attach_with_port_routes_through_submit(self):
        with mock.patch.object(bs, "_submit",
                               return_value="Attached.") as submit, \
                mock.patch.object(bs, "_impl_attach_cdp",
                                  return_value="Attached port.") as impl:
            self.assertEqual(be._attach(9333), "Attached.")
            submit.assert_called_once()
            args, kwargs = submit.call_args
            self.assertEqual(kwargs.get("kind"), "launch")
            self.assertEqual(args[0](), "Attached port.")
            impl.assert_called_once_with(9333)

    def test_status_line_reads_on_the_browser_worker_thread(self):
        # _impl_status reads Playwright page objects: on the
        # caller's thread the titles read '?' — the status must
        # be read through _submit, kind="probe" (a read-only
        # peek: a timeout means "couldn't look right now" and
        # must never reset the session).
        with mock.patch.object(bs, "_submit",
                               return_value="Connected.") as submit, \
                mock.patch.object(bs, "_impl_status") as impl:
            self.assertEqual(be._status_line(), "Connected.")
            submit.assert_called_once_with(bs._impl_status, kind="probe")
            impl.assert_not_called()


class ConnectPathTests(unittest.TestCase):
    """browser_connect_path() (field-check #45): the wizard's
    browser page names the path this machine takes — the same
    decision tree as enable_browser_control(), minus the flip
    and the attach, so it never enables anything."""

    def test_attached_names_the_connection(self):
        _fake_enable_env(self, connected=True)
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "attached")
        self.assertIn("already connected", result["line"])

    def test_chrome_answering_is_direct(self):
        _fake_enable_env(self, connected=False, handshake=_CHROME_HS)
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "direct")
        self.assertIn("drive your Chrome as it is", result["line"])

    def test_toggle_port_answering_is_direct(self):
        _fake_enable_env(self, connected=False, handshake=None)
        be._read_devtools_active_port.return_value = 9333
        be._probe_cdp.side_effect = lambda port: (
            _CHROME_HS if port == 9333 else None)
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "direct")

    def test_chrome_running_without_a_port_is_toggle_or_restart(self):
        _fake_enable_env(self, connected=False, handshake=None,
                         running=True)
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "toggle_or_restart")
        self.assertIn("one switch", result["line"])
        self.assertIn("separate VYUK profile", result["line"])

    def test_chrome_not_running_is_launch(self):
        _fake_enable_env(self, connected=False, handshake=None,
                         running=False)
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "launch")
        self.assertIn("separate VYUK profile", result["line"])

    def test_chrome_state_unknown_is_unknown(self):
        _fake_enable_env(self, connected=False, handshake=None,
                         running=None)
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "unknown")
        self.assertIn("will check", result["line"])

    def test_foreign_answer_on_the_port_is_unknown(self):
        _fake_enable_env(self, connected=False, handshake=_OTHER_HS)
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "unknown")

    def test_probe_failure_is_unknown_not_a_raise(self):
        # A probe that raises reads as a closed port — the tree
        # continues from the Chrome-running check. A failure
        # outside the guarded probes reads unknown, never raises.
        _fake_enable_env(self, connected=False, handshake=None,
                         running=False)
        be._probe_cdp.side_effect = RuntimeError("boom")
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "launch")
        be._is_connected.side_effect = RuntimeError("boom")
        result = be.browser_connect_path()
        self.assertEqual(result["path"], "unknown")

    def test_path_lines_are_jargon_free(self):
        # The lines are user-facing copy; keep them out of the
        # first-open jargon list even though they live in core/.
        from core.firstopen_optin import JARGON_TERMS
        import re as _re

        envs = [
            dict(connected=True),
            dict(handshake=_CHROME_HS),
            dict(handshake=None, running=True),
            dict(handshake=None, running=False),
            dict(handshake=None, running=None),
        ]
        lines = []
        for env in envs:
            with mock.patch.object(
                    be, "_is_connected",
                    return_value=env.get("connected", False)), \
                    mock.patch.object(be, "_port", return_value=9222), \
                    mock.patch.object(
                        be, "_probe_cdp",
                        return_value=env.get("handshake")), \
                    mock.patch.object(
                        be, "_chrome_running",
                        return_value=env.get("running")), \
                    mock.patch.object(
                        be, "_read_devtools_active_port",
                        return_value=None):
                lines.append(be.browser_connect_path()["line"])
        self.assertEqual(len(lines), 5)
        for line in lines:
            hits = [t for t in JARGON_TERMS
                    if _re.search(r"\b" + _re.escape(t) + r"\b", line,
                                  flags=_re.IGNORECASE)]
            self.assertEqual(hits, [],
                             msg="jargon in path line: %r" % line)


if __name__ == "__main__":
    unittest.main()
