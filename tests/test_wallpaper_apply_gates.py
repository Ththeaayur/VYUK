"""Wallpaper-apply gates batch: the four ruled gates, honestly reported.

Ruled 2026-11-05 ("same gates, honestly reported"): the closing
apply step runs behind opt-in on AND pilot reports available AND
host Windows AND the funnel confirm gate approved. On any gate
failure the handler still resolves (state + art) and says why
nothing was applied — it never claims applied on a mere resolve.
Unavailable art is never applied. This suite pins that contract
through the funnel, on both interpreters (the apply handler
imports only stdlib + core.wallpaper, like the existing
test_wallpaper_apply.py harness this file mirrors).

Gate order pinned here: confirm (funnel, before the handler) >
opt-in > pilot disposition > art checks > Windows tail.
"""

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
from core.funnel.tools_registry import tools
from core.config import config as app_config

_DATA_DIR = ROOT / "data"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


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


class TestWallpaperApplyGates(FunnelBase):
    def _entries(self, tmp, name="day.png", data=PNG_BYTES, state="day"):
        return [{"state": state,
                 "art_path": _write(pathlib.Path(tmp) / name, data)}]

    def _patched_windows(self, calls):
        return (mock.patch.object(wallpaper_mod, "is_windows",
                                  return_value=True),
                mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True))

    def test_opt_in_off_by_default_refuses_with_reason(self):
        # INTENTIONAL (wallpaper-apply gates): opt_in defaults to
        # False, and the default call must stop at the gate even
        # with approval + patched Windows + valid art.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            win, seam = self._patched_windows(calls)
            with win, seam:
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries},
                                   approve=True)
            self.assertEqual(result["status"], "success", result)
            self.assertIn("Wallpaper state for this hour: day",
                          result["result"])
            self.assertIn(entries[0]["art_path"], result["result"])
            self.assertIn("Applied: no", result["result"])
            self.assertIn("wallpaper-opt-in-off", result["result"])
            self.assertNotIn("Wallpaper applied", result["result"])
            self.assertEqual(calls, [])

    def test_opt_in_false_explicit_refuses(self):
        # INTENTIONAL: an explicit opt_in False is a gate failure,
        # not a missing argument.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            win, seam = self._patched_windows(calls)
            with win, seam:
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries,
                                    "opt_in": False},
                                   approve=True)
            self.assertIn("wallpaper-opt-in-off", result["result"])
            self.assertEqual(calls, [])

    def test_pilot_not_pilot_refuses(self):
        # INTENTIONAL: the pilot gate mirrors resolution's
        # disposition check; a non-PILOT pilot refuses.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            win, seam = self._patched_windows(calls)
            with win, seam, mock.patch.object(
                    wallpaper_mod, "pilot_status",
                    return_value={"disposition": "DISABLED",
                                  "available_here": True}):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries,
                                    "opt_in": True},
                                   approve=True)
            self.assertIn("Wallpaper state for this hour: day",
                          result["result"])
            self.assertIn("pilot-status-not-pilot", result["result"])
            self.assertNotIn("Wallpaper applied", result["result"])
            self.assertEqual(calls, [])

    def test_gate_order_opt_in_before_pilot(self):
        # INTENTIONAL: with both gates failing, the opt-in reason
        # wins — gates run opt-in first, as resolution does.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            win, seam = self._patched_windows(calls)
            with win, seam, mock.patch.object(
                    wallpaper_mod, "pilot_status",
                    return_value={"disposition": "DISABLED",
                                  "available_here": True}):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries},
                                   approve=True)
            self.assertIn("wallpaper-opt-in-off", result["result"])
            self.assertNotIn("pilot-status-not-pilot",
                             result["result"])
            self.assertEqual(calls, [])

    def test_windows_gate_after_opt_in_and_pilot(self):
        # INTENTIONAL: opt-in on + pilot PILOT (unpatched, always
        # PILOT) + real Linux (no is_windows patch) stops at the
        # Windows tail, seam never called.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries,
                                    "opt_in": True},
                                   approve=True)
            self.assertIn("not Windows", result["result"])
            self.assertNotIn("Wallpaper applied", result["result"])
            self.assertEqual(calls, [])

    def test_all_gates_pass_applies(self):
        # INTENTIONAL: opt-in on + pilot PILOT + patched Windows +
        # approval + valid art is the one path that applies, and
        # it applies exactly the resolved file.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            win, seam = self._patched_windows(calls)
            with win, seam:
                result = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries,
                                    "opt_in": True},
                                   approve=True)
            self.assertIn("Wallpaper applied for state day",
                          result["result"])
            self.assertEqual(
                calls,
                [str(pathlib.Path(entries[0]["art_path"]).resolve())])

    def test_gate_failures_never_claim_applied(self):
        # INTENTIONAL: no failure path may contain the applied
        # claim string — the honest-reporting contract.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            win, seam = self._patched_windows(calls)
            with win, seam:
                no_opt = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries},
                                   approve=True)
            self.assertNotIn("Wallpaper applied", no_opt["result"])
            with win, seam, mock.patch.object(
                    wallpaper_mod, "pilot_status",
                    return_value={"disposition": "DISABLED",
                                  "available_here": True}):
                no_pilot = self.call("wallpaper_apply",
                                     {"hour": 10, "entries": entries,
                                      "opt_in": True},
                                     approve=True)
            self.assertNotIn("Wallpaper applied", no_pilot["result"])
            with mock.patch.object(
                    wallpaper_mod, "_set_wallpaper_via_spi",
                    side_effect=lambda p: calls.append(p) or True):
                no_win = self.call("wallpaper_apply",
                                   {"hour": 10, "entries": entries,
                                    "opt_in": True},
                                   approve=True)
            self.assertNotIn("Wallpaper applied", no_win["result"])
            self.assertEqual(calls, [])

    def test_unavailable_art_still_refused_with_gates_on(self):
        # INTENTIONAL: gates on must not swallow the art rules —
        # missing art and escaping art still refuse, seam silent.
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            win, seam = self._patched_windows(calls)
            gone = [{"state": "day",
                     "art_path": str(pathlib.Path(tmp) / "gone.png")}]
            with win, seam:
                missing = self.call("wallpaper_apply",
                                    {"hour": 10, "entries": gone,
                                     "opt_in": True},
                                    approve=True)
            self.assertIn("missing", missing["result"])
            art_dir = pathlib.Path(tmp) / "art"
            _write(art_dir / "day.png", PNG_BYTES)
            with win, seam:
                escaped = self.call(
                    "wallpaper_apply",
                    {"hour": 10,
                     "entries": [{"state": "day",
                                  "art_path": "../outside.png"}],
                     "art_dir": str(art_dir), "opt_in": True},
                    approve=True)
            self.assertIn("escapes the art directory",
                          escaped["result"])
            self.assertEqual(calls, [])

    def test_confirm_denied_still_refuses_before_gates(self):
        # INTENTIONAL: the confirm gate is the outermost gate —
        # without approval the funnel refuses before the handler
        # (and its opt-in/pilot gates) ever runs.
        with tempfile.TemporaryDirectory() as tmp:
            entries = self._entries(tmp)
            calls = []
            win, seam = self._patched_windows(calls)
            with win, seam:
                refused = self.call("wallpaper_apply",
                                    {"hour": 10, "entries": entries,
                                     "opt_in": True},
                                    approve=False)
            self.assertEqual(refused["status"], "refused", refused)
            self.assertEqual(refused["kind"], "not_confirmed")
            self.assertEqual(calls, [])

    def test_apply_schema_declares_opt_in_and_registry_756(self):  # INTENTIONAL — Click-anywhere (2026-11-14): stale method-name label renamed; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        # INTENTIONAL: the opt_in schema property is what moves
        # the schema-lint total 1481 -> 1482 (see
        # test_auditfix_batch2); registry gains no tool.
        # Revert batch (2026-11-05) INTENTIONAL: 655, not 654 —
        # wallpaper_revert added, confirm-gated.
        self.assertEqual(len(tools.get_declarations()), 756)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        decl = next(d for d in tools.get_declarations()
                    if d["name"] == "wallpaper_apply")
        opt_in = decl["parameters"]["properties"]["opt_in"]
        self.assertEqual(opt_in["type"], "boolean")
        self.assertIn("description", opt_in)
        self.assertNotIn("opt_in", decl["parameters"].get("required",
                                                          []))


if __name__ == "__main__":
    unittest.main()
