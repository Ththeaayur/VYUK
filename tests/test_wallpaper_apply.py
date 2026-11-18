"""Wallpaper-apply batch (2026-11-03): wallpaper_apply funnel tool.

wallpaper_state resolves only; wallpaper_apply is the separate
confirm-gated tool that sets what resolution resolved, once, on
demand, after human confirmation. This suite pins:

* gate class: confirm-gated (never read-only, never free), registry
  654, G2 snapshot entry {read False, confirm True, free None};
* resolution identity: apply sets exactly the file wallpaper_state
  reports for the same hour + entries;
* validation order (all before any Windows call): bad hour/entries
  sentences mirror wallpaper_state; no art entry, missing file,
  non-image (suffix AND magic bytes), bundled art (inside the
  app's own package tree), and art escaping a supplied art_dir
  all refuse honestly with the seam never called;
* Windows seam: without approval the gate refuses and the seam is
  never called; with approval + patched Windows the seam records
  the resolved absolute path; a refusing seam propagates
  honestly; on real Linux (no patch) the answer is windows-only
  and the seam is never called;
* field validation on real Windows (actual desktop change,
  persistence flags, multi-monitor behaviour) is OWED — the lab
  fakes prove the decision/validation/wiring logic only, never a
  real desktop change.

The handler imports only stdlib + core.wallpaper (no w9_wiring),
so this suite runs fully on both interpreters.
"""

import ast
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import confirm_gate, injection
from core import wallpaper as wallpaper_mod
from core.funnel import tools_gate as tools_mod
from core.funnel import tools_gate_confirm as gate_confirm
from core.funnel import tools_gate_readonly as gate_readonly
from core.funnel.tools_registry import tools
from core.config import config as app_config

_DATA_DIR = ROOT / "data"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPG_BYTES = b"\xff\xd8\xff" + b"\x00" * 16
BMP_BYTES = b"BM" + b"\x00" * 16


class DataGuard:
    """Snapshot/restore the ENTIRE working data/ tree per test."""

    def __init__(self):
        self._saved = {}

    def _scan(self):
        found = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(_DATA_DIR)] = path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    (_DATA_DIR / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = _DATA_DIR / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


class FunnelBase(unittest.TestCase):
    def setUp(self):
        self._guard = DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False
        # Revert batch (2026-11-05) INTENTIONAL: apply_wallpaper
        # now saves the previous desktop before the SPI call, so
        # every is_windows-patched test needs the save seam fed:
        # a fake current desktop + the record pointed at a
        # scratch file (the real save runs through the funnel).
        self._tmpdir = tempfile.TemporaryDirectory()
        self._patch_prev = mock.patch.object(
            wallpaper_mod, "_PATH_OVERRIDE",
            pathlib.Path(self._tmpdir.name) / "wallpaper_previous.json")
        self._patch_read = mock.patch.object(
            wallpaper_mod, "_read_current_wallpaper",
            return_value={"image": str(ROOT / "old_wallpaper.png"),
                          "style": "10", "tile": "0"})
        self._patch_prev.start()
        self._patch_read.start()

    def tearDown(self):
        self._patch_read.stop()
        self._patch_prev.stop()
        self._tmpdir.cleanup()
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def call(self, name, args, approve=False):
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


class TestWallpaperApplyGate(FunnelBase):
    def test_gate_class(self):
        self.assertIn("wallpaper_apply",
                      gate_confirm._CONFIRM_GATE_TOOLS)
        self.assertTrue(
            tools_mod._needs_confirmation("wallpaper_apply", {}))
        self.assertNotIn("wallpaper_apply",
                         gate_readonly._READ_ONLY_TOOLS)
        self.assertNotIn("wallpaper_apply",
                         tools_mod._FREE_BY_DESIGN)
        # wallpaper_state keeps its read-only class untouched.
        self.assertTrue(
            tools_mod._is_read_class("wallpaper_state", {}))
        self.assertFalse(
            tools_mod._needs_confirmation("wallpaper_state", {}))

    def test_registry_and_snapshot(self):
        # Revert batch (2026-11-05) INTENTIONAL: 655, not 654 —
        # wallpaper_revert added, confirm-gated.
        self.assertEqual(len(tools.get_declarations()), 756)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        snap = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json"
             ).read_text(encoding="utf-8"))
        self.assertEqual(
            snap["wallpaper_apply"],
            {"confirm": True, "free": None, "read": False})
        self.assertEqual(
            snap["wallpaper_state"],
            {"confirm": False, "free": None, "read": True})

    def test_pilot_status_wired_field_validation_owed(self):
        status = wallpaper_mod.pilot_status()
        self.assertEqual(status["disposition"], "PILOT")
        self.assertTrue(status["wired"])
        self.assertIn("field validation", status["closing_step"])


class TestWallpaperApplyFunnel(FunnelBase):
    # Wallpaper-apply gates (ruled 2026-11-05): approve-path
    # calls pass opt_in=True — the handler now requires
    # opt-in on (plus pilot PILOT, Windows, confirm) before
    # it applies; without opt_in these calls would stop at
    # the gate. The gate itself is pinned by the new
    # tests/test_wallpaper_apply_gates.py.

    def _entries(self, tmp, name="day.png", data=PNG_BYTES, state="day"):
        return [{"state": state,
                 "art_path": _write(pathlib.Path(tmp) / name, data)}]

    def test_refused_without_approval_seam_never_called(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                refused = self.call("wallpaper_apply",
                                    {"hour": 10, "entries": entries})
            self.assertEqual(refused["status"], "refused", refused)
            self.assertEqual(refused["kind"], "not_confirmed")
            self.assertEqual(calls, [])

    def test_apply_through_seam_sets_resolved_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            with mock.patch.object(wallpaper_mod, "is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "_set_wallpaper_via_spi",
                        side_effect=lambda p: calls.append(p) or True):
                applied = self.call("wallpaper_apply",
                                    {"hour": 10, "entries": entries, "opt_in": True},
                                    approve=True)
            self.assertEqual(applied["status"], "success", applied)
            self.assertIn("Wallpaper applied for state day",
                          applied["result"])
            # Resolution identity, without the funnel: apply resolves
            # with the same primitives resolution uses
            # (state_for_hour + resolve_art), so the applied file IS
            # the resolved file — no w9_wiring import needed, so
            # this runs on both interpreters.
            self.assertEqual(wallpaper_mod.state_for_hour(10), "day")
            self.assertEqual(
                wallpaper_mod.resolve_art(
                    "day",
                    [wallpaper_mod.WallpaperEntry(
                        state="day",
                        art_path=entries[0]["art_path"])]),
                entries[0]["art_path"])
            self.assertEqual(
                calls,
                [str(pathlib.Path(entries[0]["art_path"]).resolve())])

    def test_spi_refusal_propagates_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            with mock.patch.object(wallpaper_mod, "is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "_set_wallpaper_via_spi",
                        return_value=False):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries, "opt_in": True},
                                   approve=True)
            # B1 (INTENTIONAL): the refused apply is a failure
            # dict now, never a success payload — the message
            # text is unchanged.
            self.assertEqual(result["status"], "failure", result)
            self.assertEqual(result["kind"], "wallpaper_not_set", result)
            self.assertIn("not applied", result["message"])
            self.assertIn("system-call-refused", result["message"])

    def test_windows_only_on_real_linux_seam_never_called(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries, "opt_in": True},
                                   approve=True)
            self.assertEqual(result["status"], "success", result)
            self.assertIn("not Windows", result["result"])
            self.assertEqual(calls, [])

    def test_missing_file_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [{"state": "day",
                        "art_path": str(pathlib.Path(tmp) / "gone.png")}]
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries, "opt_in": True},
                                   approve=True)
            self.assertIn("missing", result["result"])
            self.assertEqual(calls, [])

    def test_non_image_suffix_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp, name="day.txt",
                                    data=b"not an image at all")
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries, "opt_in": True},
                                   approve=True)
            self.assertIn("not a wallpaper image", result["result"])
            self.assertEqual(calls, [])

    def test_magic_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp, name="day.png",
                                    data=b"this is text, not a png")
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries, "opt_in": True},
                                   approve=True)
            self.assertIn("not a wallpaper image", result["result"])
            self.assertEqual(calls, [])

    def test_uppercase_suffix_and_jpeg_bmp_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = [("day.PNG", PNG_BYTES), ("day.jpg", JPG_BYTES),
                     ("day.bmp", BMP_BYTES)]
            for name, data in cases:
                entries = self._entries(tmp, name=name, data=data)
                calls = []
                with mock.patch.object(wallpaper_mod, "is_windows",
                                       return_value=True), \
                        mock.patch.object(
                            wallpaper_mod, "_set_wallpaper_via_spi",
                            side_effect=lambda p: calls.append(p) or True):
                    result = self.call("wallpaper_apply",
                                       {"hour": 10, "entries": entries, "opt_in": True},
                                       approve=True)
                self.assertIn("Wallpaper applied", result["result"],
                              (name, result))
                self.assertEqual(len(calls), 1, name)

    def test_no_art_entry_for_state_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp, state="night")
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries, "opt_in": True},
                                   approve=True)
            self.assertIn("No art entry for state 'day'",
                          result["result"])
            self.assertEqual(calls, [])

    def test_bad_hour_and_entries_mirror_state(self):
        bad_hour = self.call("wallpaper_apply", {"hour": 25},
                             approve=True)
        self.assertIn("0 to 23", bad_hour["result"])
        # A non-list entries value never reaches the handler: the
        # funnel schema gate refuses it first (refused / validation).
        bad_entries = self.call("wallpaper_apply",
                                {"hour": 10, "entries": "not-a-list"},
                                approve=True)
        self.assertEqual(bad_entries["status"], "refused", bad_entries)
        self.assertEqual(bad_entries["kind"], "validation")
        # The handler's own mapping check still stands for direct
        # callers, behind the schema gate — pinned here through the
        # module-level core function (GAPS-23 F-V7), the same code
        # the funnel delegates to.
        from core.funnel.tools_handlers_pack_libwire import wallpaper_apply_core  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
        text = wallpaper_apply_core(
            hour=10, entries="not-a-list")
        self.assertIn("must be a list", text)
        bad_state = self.call(
            "wallpaper_apply",
            {"hour": 10,
             "entries": [{"state": "nope", "art_path": "/x/y.jpg"}]},
            approve=True)
        self.assertIn("Unknown wallpaper state 'nope'",
                      bad_state["result"])

    def test_schema_rejects_bad_args_before_handler(self):
        bad = self.call("wallpaper_apply", {"hour": "ten"},
                        approve=True)
        self.assertEqual(bad["status"], "refused", bad)
        self.assertEqual(bad["kind"], "validation")

    def test_bundled_art_refuses(self):
        tree_png = str(ROOT / "gui" / "assets" / "vyuk_icon.png")
        self.assertTrue(pathlib.Path(tree_png).is_file(), tree_png)
        entries = [{"state": "day", "art_path": tree_png}]
        calls = []
        with mock.patch.object(wallpaper_mod, "is_windows",
                               return_value=True), \
                mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
            result = self.call("wallpaper_apply",
                               {"hour": 10, "entries": entries, "opt_in": True},
                               approve=True)
        self.assertIn("package tree", result["result"])
        self.assertIn("never bundled art", result["result"])
        self.assertEqual(calls, [])

    def test_art_dir_relative_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            art_dir = pathlib.Path(tmp) / "art"
            _write(art_dir / "day.png", PNG_BYTES)
            calls = []
            with mock.patch.object(wallpaper_mod, "is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "_set_wallpaper_via_spi",
                        side_effect=lambda p: calls.append(p) or True):
                applied = self.call(
                    "wallpaper_apply",
                    {"hour": 10,
                     "entries": [{"state": "day", "art_path": "day.png"}],
                     "art_dir": str(art_dir), "opt_in": True},
                    approve=True)
            self.assertIn("Wallpaper applied", applied["result"])
            self.assertEqual(
                calls, [str((art_dir / "day.png").resolve())])
            escaped = self.call(
                "wallpaper_apply",
                {"hour": 10,
                 "entries": [{"state": "day",
                              "art_path": "../outside.png"}],
                 "art_dir": str(art_dir), "opt_in": True},
                approve=True)
            self.assertIn("escapes the art directory",
                          escaped["result"])
            self.assertEqual(len(calls), 1)

    def test_art_dir_inside_tree_still_bundled(self):
        tree_art = ROOT / "gui" / "assets"
        calls = []
        with mock.patch.object(wallpaper_mod, "is_windows",
                               return_value=True), \
                mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
            result = self.call(
                "wallpaper_apply",
                {"hour": 10,
                 "entries": [{"state": "day",
                              "art_path": "vyuk_icon.png"}],
                 "art_dir": str(tree_art), "opt_in": True},
                approve=True)
        self.assertIn("package tree", result["result"])
        self.assertEqual(calls, [])

    def test_resolution_only_calls_unchanged(self):
        # No-Qt-safe resolution paths: wallpaper_state's handler
        # validates hour/entries before its w9_wiring import, so
        # these sentences hold on both interpreters. The full
        # resolution success path is pinned by the existing
        # Qt-only suite (test_resolves_state_never_applies) and
        # runs as a neighbour; the primitives apply uses are
        # pinned directly here.
        bad_hour = self.call("wallpaper_state", {"hour": 25})
        self.assertEqual(bad_hour["status"], "success", bad_hour)
        self.assertIn("0 to 23", bad_hour["result"])
        bad_state = self.call(
            "wallpaper_state",
            {"hour": 10,
             "entries": [{"state": "nope", "art_path": "/x/y.jpg"}]})
        self.assertEqual(bad_state["status"], "success", bad_state)
        self.assertIn("Unknown wallpaper state 'nope'",
                      bad_state["result"])
        self.assertEqual(wallpaper_mod.state_for_hour(10), "day")
        self.assertIsNone(
            wallpaper_mod.resolve_art(
                "day",
                [wallpaper_mod.WallpaperEntry(state="night",
                                              art_path="/x/n.png")]))

    def test_seam_wiring_pinned_in_source(self):
        # Pins the production wiring the lab cannot execute: the
        # seam carries SPI_SETDESKWALLPAPER with UPDATEINIFILE |
        # SENDCHANGE, and apply_wallpaper calls the seam. The
        # actual desktop change is field work, not lab work.
        src = (ROOT / "core" / "wallpaper.py").read_text()
        tree = ast.parse(src)
        seam = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef)
                    and n.name == "_set_wallpaper_via_spi")
        seam_src = ast.get_source_segment(src, seam)
        self.assertIn("SPI_SETDESKWALLPAPER", seam_src)
        self.assertIn("SPIF_UPDATEINIFILE", seam_src)
        self.assertIn("SPIF_SENDCHANGE", seam_src)
        apply_fn = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef)
                        and n.name == "apply_wallpaper")
        self.assertIn("_set_wallpaper_via_spi",
                      ast.get_source_segment(src, apply_fn))


if __name__ == "__main__":
    unittest.main()
