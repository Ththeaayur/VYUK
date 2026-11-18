"""B-D8 (2026-11-09) app management via winget.

Runs against a FAKE winget backend (no real winget.exe on this
Linux lab machine) that returns canned winget table output in the
exact shape the tree's own parsers read (verified against
``_winget_rows`` before writing the suite). The
engine-availability tests assert the honest engine_unavailable
degradation on this machine instead of skipping: a suite that
silently skips the app-management checks on an interpreter
without winget is a lie, not a pass.

The real-Windows leg — a real install/update/remove on his
machine — is a field leg, owed, not this batch.
"""

import unittest

from core.desktop import app_management as am
from core.skills_plus_apps_files import list_installed_apps, uninstall_app
from core.skills_system_apps_startup import (
    list_app_updates, winget_install, winget_upgrade_all)

SEARCH_VLC = """Name                 Id                    Version   Source
-------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    winget
"""

SEARCH_AMBIG = """Name                 Id                    Version   Source
-------------------------------------------------------------------
NotePad              AppA.NotePad          1.0       winget
NotePad              AppB.NotePad          2.0       winget
"""

SEARCH_WINGET_ITSELF = """Name                      Id                           Version   Source
------------------------------------------------------------------------
Windows Package Manager   Microsoft.DesktopAppInstaller  1.0       winget
"""

SEARCH_TWO = """Name                 Id                    Version   Source
-------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    winget
VLC Streamer         Hobbyist.VLCStreamer  1.5       winget
"""

LIST_VLC = """Name                 Id                    Version   Source
-------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    winget
"""

LIST_VLC_NEW = """Name                 Id                    Version   Source
-------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.21    winget
"""

LIST_EMPTY = "No installed package found matching input criteria.\n"

LIST_ALL = """Name                 Id                    Version   Source
-------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    winget
7-Zip                7zip.7zip             23.01     winget
"""

UPGRADE_PENDING = """Name                 Id                    Version   Available Source
--------------------------------------------------------------------------------
VLC media player     VideoLAN.VLC          3.0.20    3.0.21    winget
7-Zip                7zip.7zip             23.01     24.09     winget
"""

UPGRADE_NONE = "No available upgrade found.\n"

GARBAGE = "this is not a winget table at all\njust three lines of noise\nnone of them a table\n"


class _FakeWinget:
    """Canned winget.exe: dispatches on the subcommand, records
    every call (so refusals can assert nothing was sent), and
    models state — an upgraded package leaves the pending list
    and its installed version moves, like real winget."""

    def __init__(self, *, present=True, fail_install=False,
                 fail_uninstall=False, fail_upgrade=False,
                 pending=True, installed_version="3.0.20"):
        self.present = present
        self.fail_install = fail_install
        self.fail_uninstall = fail_uninstall
        self.fail_upgrade = fail_upgrade
        self.pending = pending
        self.installed_version = installed_version
        self.upgraded = set()
        self.calls = []

    def which(self):
        return self.present

    def _list_out(self):
        if self.installed_version is None:
            return LIST_EMPTY
        return LIST_VLC.replace("3.0.20", self.installed_version)

    def _pending_out(self):
        if not self.pending:
            return UPGRADE_NONE
        rows = []
        if "VideoLAN.VLC" not in self.upgraded:
            rows.append("VLC media player     VideoLAN.VLC          3.0.20    3.0.21    winget")
        if "7zip.7zip" not in self.upgraded:
            rows.append("7-Zip                7zip.7zip             23.01     24.09     winget")
        if not rows:
            return UPGRADE_NONE
        return (
            "Name                 Id                    Version   Available Source\n"
            "--------------------------------------------------------------------------------\n"
            + "\n".join(rows) + "\n"
        )

    def run(self, args, timeout_s, cancel_token=None):
        self.calls.append(list(args))
        sub = args[1] if len(args) > 1 else ""
        if sub == "search":
            query = args[3] if len(args) > 3 else ""
            if query.lower() in ("vlc", "videolan.vlc", "vlc media player"):
                return 0, SEARCH_VLC, ""
            if query.lower() == "notepad":
                return 0, SEARCH_AMBIG, ""
            if query.lower() == "microsoft.desktopappinstaller":
                return 0, SEARCH_WINGET_ITSELF, ""
            if query.lower() == "two":
                return 0, SEARCH_TWO, ""
            if query.lower() == "garbage":
                return 0, GARBAGE, ""
            return 0, "No package found matching input criteria.\n", ""
        if sub == "list":
            if "--id" in args:
                return 0, self._list_out(), ""
            return 0, LIST_ALL, ""
        if sub == "upgrade" and "--all" in args:
            if self.fail_upgrade:
                return 1, "", "Something went wrong.\n"
            self.upgraded.update({"VideoLAN.VLC", "7zip.7zip"})
            return 0, "All packages upgraded.\n", ""
        if sub == "upgrade" and "--id" in args:
            if self.fail_upgrade:
                return 1, "", "Upgrade failed.\n"
            self.upgraded.add(args[args.index("--id") + 1])
            self.installed_version = "3.0.21"
            return 0, "Successfully installed\n", ""
        if sub == "upgrade":
            return 0, self._pending_out(), ""
        if sub == "install":
            if self.fail_install:
                return 1, "", "0x80073D02 : Install failed.\n"
            self.installed_version = "3.0.20"
            return 0, "Successfully installed\n", ""
        if sub == "uninstall":
            if self.fail_uninstall:
                return 1, "", "Uninstall failed.\n"
            self.installed_version = None
            return 0, "Successfully uninstalled\n", ""
        return 1, "", "unknown command"


def _mgr(**kw):
    return am.AppManagement(backend=_FakeWinget(**kw))


class _CancellingWinget(_FakeWinget):
    """B-D8r: the mutating runs return the B20 -3 sentinel (the
    child was stopped), while search + pending read fine."""

    def run(self, args, timeout_s, cancel_token=None):
        if args[1] == "install" or (args[1] == "upgrade" and "--all" in args):
            self.calls.append(list(args))
            return -3, "", "cancelled"
        return super().run(args, timeout_s, cancel_token)


class TestSearchResolve(unittest.TestCase):
    def test_search_returns_candidates(self):
        r = _mgr().search("VLC")
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["candidates"]), 1)
        self.assertEqual(r["candidates"][0]["id"], "VideoLAN.VLC")
        self.assertEqual(r["candidates"][0]["version"], "3.0.20")
        self.assertEqual(r["candidates"][0]["source"], "winget")

    def test_search_no_results_is_a_failure(self):
        r = _mgr().search("no-such-app-xyz")
        self.assertFalse(r["ok"])
        self.assertEqual(r["candidates"], [])
        self.assertIn("no package matching", r["reason"])

    def test_search_unreadable_is_a_failure_never_empty(self):
        r = _mgr().search("garbage")
        self.assertFalse(r["ok"])
        self.assertIn("couldn't be read", r["reason"])

    def test_search_empty_query_refuses(self):
        r = _mgr().search("   ")
        self.assertFalse(r["ok"])
        self.assertIn("needs the name or package id", r["reason"])

    def test_resolve_exact_id_wins(self):
        r = _mgr().resolve("VideoLAN.VLC")
        self.assertTrue(r["ok"])
        self.assertEqual(r["package"]["id"], "VideoLAN.VLC")

    def test_resolve_unique_exact_name_wins(self):
        r = _mgr().resolve("VLC media player")
        self.assertTrue(r["ok"])
        self.assertEqual(r["package"]["id"], "VideoLAN.VLC")

    def test_resolve_ambiguous_exact_name_refuses_with_candidates(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.resolve("NotePad")
        self.assertFalse(r["ok"])
        self.assertIsNone(r["package"])
        self.assertEqual(len(r["candidates"]), 2)
        self.assertEqual(
            {c["id"] for c in r["candidates"]},
            {"AppA.NotePad", "AppB.NotePad"},
        )
        self.assertIn("different packages", r["reason"])
        # No install was attempted: only the search ran.
        self.assertEqual([c[1] for c in fake.calls], ["search"])

    def test_resolve_no_exact_match_refuses_with_candidates(self):
        r = _mgr().resolve("two")
        self.assertFalse(r["ok"])
        self.assertEqual(len(r["candidates"]), 2)
        self.assertIn("No exact match", r["reason"])


class TestInstall(unittest.TestCase):
    def test_install_without_confirmation_refuses_and_sends_nothing(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.install("VideoLAN.VLC")
        self.assertFalse(r["ok"])
        self.assertIn("confirm=true", r["reason"])
        # Only the resolve search ran; no install call.
        self.assertEqual([c[1] for c in fake.calls], ["search"])
        self.assertEqual(r["state"], "failed")

    def test_install_truthy_but_not_true_refuses(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.install("VideoLAN.VLC", confirm=1)
        self.assertFalse(r["ok"])
        self.assertIn("confirm=true", r["reason"])
        self.assertEqual([c[1] for c in fake.calls], ["search"])

    def test_install_with_confirmation_reports_id_and_landed_version(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.install("VideoLAN.VLC", confirm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "installed")
        self.assertEqual(r["package_id"], "VideoLAN.VLC")
        self.assertEqual(r["version"], "3.0.20")
        self.assertEqual(r["source"], "winget")
        # The install ran with --id --exact, and the landed version
        # was read back from winget list.
        install_calls = [c for c in fake.calls if c[1] == "install"]
        self.assertEqual(len(install_calls), 1)
        self.assertIn("--id", install_calls[0])
        self.assertIn("VideoLAN.VLC", install_calls[0])
        self.assertIn("--exact", install_calls[0])
        self.assertTrue(any(c[1] == "list" for c in fake.calls))

    def test_install_reported_success_but_not_in_list_is_a_failure(self):
        class _LyingWinget(_FakeWinget):
            def run(self, args, timeout_s, cancel_token=None):
                if args[1] == "install":
                    self.calls.append(list(args))
                    return 0, "Successfully installed\n", ""
                if args[1] == "list":
                    self.calls.append(list(args))
                    return 0, LIST_EMPTY, ""
                return super().run(args, timeout_s, cancel_token)

        r = am.AppManagement(backend=_LyingWinget()).install("VideoLAN.VLC", confirm=True)
        self.assertFalse(r["ok"])
        self.assertIn("doesn't show in winget's installed list", r["reason"])

    def test_failed_install_is_an_honest_failure(self):
        r = _mgr(fail_install=True).install("VideoLAN.VLC", confirm=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["state"], "failed")
        self.assertIn("could not install", r["reason"])
        self.assertIn("0x80073D02", r["reason"])

    def test_install_ambiguous_name_never_installs(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.install("NotePad", confirm=True)
        self.assertFalse(r["ok"])
        self.assertIn("different packages", r["reason"])
        self.assertEqual([c[1] for c in fake.calls], ["search"])


class TestUpdate(unittest.TestCase):
    def test_update_one_app_updates_only_that_app(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.update("VideoLAN.VLC", confirm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "updated")
        self.assertEqual(r["version"], "3.0.21")
        upgrade_calls = [c for c in fake.calls if c[1] == "upgrade"]
        # One --id upgrade, never --all; the pending list is read
        # before and after (two plain `upgrade` calls).
        self.assertTrue(any("--id" in c for c in upgrade_calls))
        self.assertFalse(any("--all" in c for c in upgrade_calls))

    def test_update_without_confirmation_refuses(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.update("VideoLAN.VLC")
        self.assertFalse(r["ok"])
        self.assertIn("confirm=true", r["reason"])
        self.assertEqual(fake.calls, [])

    def test_update_not_installed_refuses(self):
        fake = _FakeWinget(installed_version=None)
        mgr = am.AppManagement(backend=fake)
        r = mgr.update("VideoLAN.VLC", confirm=True)
        self.assertFalse(r["ok"])
        self.assertIn("not installed", r["reason"])
        self.assertFalse(any(c[1] == "upgrade" and "--id" in c for c in fake.calls))

    def test_update_no_pending_reports_up_to_date(self):
        fake = _FakeWinget(pending=False)
        mgr = am.AppManagement(backend=fake)
        r = mgr.update("VideoLAN.VLC", confirm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "up_to_date")
        self.assertFalse(any(c[1] == "upgrade" and "--id" in c for c in fake.calls))

    def test_update_all_is_a_separate_explicit_call(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.update_all(confirm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "updated")
        self.assertEqual(len(r["updated"]), 2)
        self.assertEqual(r["still_pending"], [])
        self.assertTrue(any("--all" in c for c in fake.calls))

    def test_update_all_without_confirmation_refuses(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.update_all()
        self.assertFalse(r["ok"])
        self.assertIn("confirm=true", r["reason"])
        self.assertEqual(fake.calls, [])


class TestRemove(unittest.TestCase):
    def test_remove_denied_package_refuses_before_any_call(self):
        for package_id in (
            "Microsoft.DesktopAppInstaller",
            "Microsoft.VCLibs.140.00.UWPDesktop",
            "Microsoft.WindowsAppRuntime.1.5",
            "Microsoft.UI.Xaml.2.8",
            "Microsoft.NET.Native.Runtime.2.2",
        ):
            fake = _FakeWinget()
            mgr = am.AppManagement(backend=fake)
            r = mgr.remove(package_id, confirm=True)
            self.assertFalse(r["ok"], package_id)
            self.assertIn("refused", r["reason"])
            self.assertEqual(fake.calls, [], package_id)

    def test_remove_with_confirmation_reports_removed_version(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.remove("VideoLAN.VLC", confirm=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "removed")
        self.assertEqual(r["package_id"], "VideoLAN.VLC")
        self.assertEqual(r["version"], "3.0.20")
        uninstall_calls = [c for c in fake.calls if c[1] == "uninstall"]
        self.assertEqual(len(uninstall_calls), 1)
        self.assertIn("--id", uninstall_calls[0])
        self.assertIn("--exact", uninstall_calls[0])

    def test_remove_without_confirmation_refuses(self):
        fake = _FakeWinget()
        mgr = am.AppManagement(backend=fake)
        r = mgr.remove("VideoLAN.VLC")
        self.assertFalse(r["ok"])
        self.assertIn("confirm=true", r["reason"])
        self.assertEqual(fake.calls, [])

    def test_remove_not_installed_refuses(self):
        fake = _FakeWinget(installed_version=None)
        mgr = am.AppManagement(backend=fake)
        r = mgr.remove("VideoLAN.VLC", confirm=True)
        self.assertFalse(r["ok"])
        self.assertIn("not installed", r["reason"])
        self.assertFalse(any(c[1] == "uninstall" for c in fake.calls))

    def test_remove_still_listed_afterwards_is_a_failure(self):
        class _StubbornWinget(_FakeWinget):
            def run(self, args, timeout_s, cancel_token=None):
                if args[1] == "uninstall":
                    self.calls.append(list(args))
                    return 0, "Successfully uninstalled\n", ""
                return super().run(args, timeout_s, cancel_token)

        r = am.AppManagement(backend=_StubbornWinget()).remove(
            "VideoLAN.VLC", confirm=True)
        self.assertFalse(r["ok"])
        self.assertIn("still in the installed list", r["reason"])


class TestListAndEngine(unittest.TestCase):
    def test_list_installed_returns_rows(self):
        r = _mgr().list_installed()
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["apps"]), 2)
        self.assertEqual(r["apps"][0]["id"], "VideoLAN.VLC")
        self.assertEqual(r["apps"][1]["id"], "7zip.7zip")

    def test_engine_unavailable_without_winget(self):
        # The real engine on this Linux lab machine: no winget.exe.
        self.assertFalse(am.engine_available())
        status = am.engine_status()
        self.assertFalse(status["available"])
        self.assertIn("engine_unavailable", status["reason"])
        mgr = am.AppManagement()
        self.assertFalse(mgr.engine_available())
        for result in (
            mgr.search("VLC"),
            mgr.resolve("VLC"),
            mgr.list_installed(),
            mgr.install("VideoLAN.VLC", confirm=True),
            mgr.update("VideoLAN.VLC", confirm=True),
            mgr.update_all(confirm=True),
            mgr.remove("VideoLAN.VLC", confirm=True),
        ):
            self.assertFalse(result["ok"])
            self.assertIn("engine_unavailable", result["reason"])


class TestRewiredTools(unittest.TestCase):
    """B-D8r: the five tools route through the B-D8 wrapper — the
    tools call the module-level wrapper functions, so the fake is
    injected via set_app_backend and reset afterwards (tearDown
    always restores the real backend, so no other suite sees it)."""

    def setUp(self):
        self._fake = _FakeWinget()
        am.set_app_backend(self._fake)

    def tearDown(self):
        am.reset_app_backend()

    def test_install_ambiguous_exact_name_refuses_and_sends_no_install(self):
        # THE HOLE, closed in the voice path: two packages sharing
        # one exact Name used to install the first, silently.
        out = winget_install("NotePad", confirm=True)
        self.assertIsInstance(out, str)
        self.assertIn("different packages", out)
        self.assertIn("AppA.NotePad", out)
        self.assertIn("AppB.NotePad", out)
        self.assertEqual([c[1] for c in self._fake.calls], ["search"])

    def test_install_unique_exact_name_installs(self):
        out = winget_install("VideoLAN.VLC", confirm=True)
        self.assertEqual(
            out,
            "Installed 'VLC media player' (package VideoLAN.VLC, version 3.0.20) "
            "via winget — confirmed in winget's installed list.")

    def test_install_without_confirm_refuses_and_keeps_source_suffix(self):
        out = winget_install("VideoLAN.VLC")
        self.assertIn("confirm=true", out)
        self.assertIn("from winget", out)
        self.assertEqual([c[1] for c in self._fake.calls], ["search"])

    def test_install_cancelled_returns_cancelled_dict(self):
        am.set_app_backend(_CancellingWinget())
        out = winget_install("VideoLAN.VLC", confirm=True)
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "cancelled")
        self.assertIn("partly installed", out.get("message", ""))

    def test_install_engine_unavailable_on_linux(self):
        am.reset_app_backend()
        out = winget_install("VideoLAN.VLC", confirm=True)
        self.assertIn("Windows package manager", out)

    def test_list_app_updates_reports_pending(self):
        out = list_app_updates()
        self.assertEqual(
            out,
            "App updates available (2):\n"
            "• VLC media player — 3.0.20 → 3.0.21 (VideoLAN.VLC)\n"
            "• 7-Zip — 23.01 → 24.09 (7zip.7zip)")

    def test_list_app_updates_up_to_date(self):
        am.set_app_backend(_FakeWinget(pending=False))
        self.assertEqual(
            list_app_updates(),
            "Everything winget manages is up to date — no app updates available.")

    def test_list_updates_wrapper_surface(self):
        r = am.AppManagement(backend=_FakeWinget()).list_updates()
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["updates"]), 2)
        self.assertEqual(r["updates"][0]["id"], "VideoLAN.VLC")
        self.assertEqual(r["updates"][0]["available"], "3.0.21")

    def test_upgrade_all_updates_everything_with_receipt(self):
        out = winget_upgrade_all(confirm=True)
        self.assertEqual(
            out,
            "winget update run finished — 2 of 2 pending app update(s) "
            "completed; no updates remain.")

    def test_upgrade_all_without_confirm_refuses(self):
        out = winget_upgrade_all()
        self.assertIn("confirm=true", out)
        self.assertEqual(self._fake.calls, [])

    def test_upgrade_all_up_to_date(self):
        am.set_app_backend(_FakeWinget(pending=False))
        self.assertEqual(
            winget_upgrade_all(confirm=True),
            "Everything winget manages is already up to date — nothing was updated.")

    def test_upgrade_all_cancelled_returns_cancelled_dict(self):
        am.set_app_backend(_CancellingWinget())
        out = winget_upgrade_all(confirm=True)
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "cancelled")
        # The tool's own wording names the check tool, not the
        # wrapper's "run the update check".
        self.assertIn("list_app_updates", out.get("message", ""))

    def test_uninstall_denied_id_refuses_before_any_winget_call(self):
        out = uninstall_app("Microsoft.DesktopAppInstaller", confirm=True)
        self.assertIn("winget itself", out)
        # Only resolve's search ran; remove refused before any call.
        self.assertEqual([c[1] for c in self._fake.calls], ["search"])

    def test_uninstall_ordinary_app_verified_removed(self):
        out = uninstall_app("VLC media player", confirm=True)
        self.assertEqual(
            out,
            "Removed 'VLC media player' (package VideoLAN.VLC, version 3.0.20) "
            "— confirmed gone from winget's installed list.")

    def test_uninstall_not_installed_reports_honestly(self):
        fake = _FakeWinget(installed_version=None)
        am.set_app_backend(fake)
        out = uninstall_app("VLC media player", confirm=True)
        self.assertIn("is not installed", out)
        self.assertFalse(any(c[1] == "uninstall" for c in fake.calls))

    def test_uninstall_ambiguous_name_refuses_with_uninstall_wording(self):
        out = uninstall_app("NotePad", confirm=True)
        self.assertIn("different packages", out)
        self.assertIn("nothing was removed", out)
        self.assertNotIn("nothing was installed", out)

    def test_uninstall_without_confirm_refuses(self):
        out = uninstall_app("VLC media player")
        self.assertIn("confirm=true", out)
        self.assertEqual(self._fake.calls, [])

    def test_list_installed_apps_lists_winget_rows(self):
        out = list_installed_apps()
        self.assertEqual(
            out,
            "Installed apps (2):\n"
            "• 7-Zip — v23.01 (package 7zip.7zip)\n"
            "• VLC media player — v3.0.20 (package VideoLAN.VLC)")

    def test_list_installed_apps_search_filters(self):
        out = list_installed_apps("vlc")
        self.assertEqual(
            out,
            "Installed apps (1):\n"
            "• VLC media player — v3.0.20 (package VideoLAN.VLC)")

    def test_list_installed_apps_limit(self):
        out = list_installed_apps(limit=1)
        self.assertEqual(
            out,
            "Installed apps (2):\n"
            "• 7-Zip — v23.01 (package 7zip.7zip)\n"
            "Showing 1 of 2 — narrow it with a search word or raise the limit.")

    def test_list_installed_apps_engine_unavailable_on_linux(self):
        am.reset_app_backend()
        out = list_installed_apps()
        self.assertIn("Windows package manager", out)


if __name__ == "__main__":
    unittest.main()
