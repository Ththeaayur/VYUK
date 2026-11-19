"""R9-4 mutagen PORT — two-way folder sync as own stdlib code.

No binary is adopted (the official mutagen release builds include
SSPL-licensed code by default — see core/two_way_sync.py — so the
suite runs entirely on temp dirs, with the trash rescue stubbed to
a temp folder). Every test below runs in the delivered state: no
operator-supplied artifact, no skips.
"""

import json
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from core import two_way_sync as tw
from core.funnel.tools_registry import tools as _registry


def _result_text(result):
    """Outcome -> its plain-words message; plain string -> itself."""
    return getattr(result, "message", result)


class _TwoWayTestCase(unittest.TestCase):
    """Base: two temp folders, temp state dir, stubbed rescue."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = pathlib.Path(self._tmp.name)
        self.a = self.tmp / "a"
        self.b = self.tmp / "b"
        self.a.mkdir()
        self.b.mkdir()
        self.trash = self.tmp / "trash"
        self.trash.mkdir()
        self.rescued = []

        def fake_rescue(victim):
            dest = self.trash / victim.name
            suffix = 0
            while dest.exists():
                suffix += 1
                dest = self.trash / f"{victim.stem} ({suffix}){victim.suffix}"
            shutil.move(str(victim), str(dest))
            self.rescued.append(str(dest))
            return dest

        self._orig_rescue = tw._rescue
        tw._rescue = fake_rescue
        self.addCleanup(setattr, tw, "_rescue", self._orig_rescue)
        self._orig_state_dir = tw._STATE_DIR
        tw._STATE_DIR = self.tmp / "state"
        self.addCleanup(setattr, tw, "_STATE_DIR", self._orig_state_dir)

    def _write(self, root, rel, text):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def _sync(self, **kwargs):
        kwargs.setdefault("folder_a", str(self.a))
        kwargs.setdefault("folder_b", str(self.b))
        return tw.sync_folders_two_way(**kwargs)


class SyncBehaviourTests(_TwoWayTestCase):
    def test_first_sync_copies_both_ways_and_records_baseline(self):
        self._write(self.a, "only_a.txt", "from A\n")
        self._write(self.b, "only_b.txt", "from B\n")
        self._write(self.a, "shared.txt", "same\n")
        self._write(self.b, "shared.txt", "same\n")
        result = self._sync()
        self.assertTrue(result.verified)
        self.assertEqual((self.b / "only_a.txt").read_text(), "from A\n")
        self.assertEqual((self.a / "only_b.txt").read_text(), "from B\n")
        state_files = list((self.tmp / "state").glob("*.json"))
        self.assertEqual(len(state_files), 1)
        state = json.loads(state_files[0].read_text(encoding="utf-8"))
        self.assertEqual(sorted(state["files"]), [
            "only_a.txt", "only_b.txt", "shared.txt"])

    def test_one_sided_change_copies_over_and_rescues_victim(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        self._write(self.a, "f.txt", "v2\n")
        result = self._sync()
        self.assertTrue(result.verified)
        self.assertEqual((self.b / "f.txt").read_text(), "v2\n")
        self.assertEqual(len(self.rescued), 1)
        self.assertEqual(pathlib.Path(self.rescued[0]).read_text(), "v1\n")

    def test_both_changed_conflict_copies_neither(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        self._write(self.a, "f.txt", "A version\n")
        self._write(self.b, "f.txt", "B version\n")
        result = self._sync()
        self.assertFalse(result.verified)
        text = _result_text(result)
        self.assertIn("CONFLICT", text)
        self.assertIn("f.txt", text)
        self.assertEqual((self.a / "f.txt").read_text(), "A version\n")
        self.assertEqual((self.b / "f.txt").read_text(), "B version\n")

    def test_both_changed_same_content_converges(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        self._write(self.a, "f.txt", "same edit\n")
        self._write(self.b, "f.txt", "same edit\n")
        result = self._sync()
        self.assertTrue(result.verified)
        self.assertNotIn("CONFLICT", _result_text(result))

    def test_delete_vs_unchanged_mirror_off_leaves_it(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        (self.a / "f.txt").unlink()
        result = self._sync()
        self.assertTrue(result.verified)
        self.assertTrue((self.b / "f.txt").exists())
        self.assertIn("left UNTOUCHED", _result_text(result))

    def test_delete_vs_unchanged_mirror_on_deletes_and_rescues(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        (self.a / "f.txt").unlink()
        result = self._sync(mirror_delete=True)
        self.assertTrue(result.verified)
        self.assertFalse((self.b / "f.txt").exists())
        self.assertEqual(len(self.rescued), 1)
        self.assertEqual(pathlib.Path(self.rescued[0]).read_text(), "v1\n")

    def test_delete_vs_modify_conflict_resurrects_nothing(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        self._write(self.a, "f.txt", "v2\n")
        (self.b / "f.txt").unlink()
        result = self._sync(mirror_delete=True)
        self.assertFalse(result.verified)
        self.assertIn("CONFLICT", _result_text(result))
        # The changed side is untouched AND the deleted side stays
        # deleted — the conflict is not silently resolved by a copy.
        self.assertEqual((self.a / "f.txt").read_text(), "v2\n")
        self.assertFalse((self.b / "f.txt").exists())

    def test_conflict_persists_across_runs(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        self._write(self.a, "f.txt", "A\n")
        self._write(self.b, "f.txt", "B\n")
        self._sync()
        result = self._sync()
        self.assertIn("CONFLICT", _result_text(result))
        self.assertEqual((self.a / "f.txt").read_text(), "A\n")
        self.assertEqual((self.b / "f.txt").read_text(), "B\n")

    def test_pending_deletion_is_not_mistaken_for_new_file(self):
        # The probe-caught bug: a deletion reported with mirror off
        # must not be copied BACK on the next run.
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        (self.a / "f.txt").unlink()
        self._sync()  # mirror off: reported, untouched
        result = self._sync(mirror_delete=True)
        self.assertFalse((self.b / "f.txt").exists())
        self.assertFalse((self.a / "f.txt").exists())
        self.assertEqual(len(self.rescued), 1)

    def test_dry_run_changes_nothing_and_writes_no_state(self):
        self._write(self.a, "f.txt", "v1\n")
        result = self._sync(dry_run=True)
        self.assertIsInstance(result, str)
        self.assertFalse((self.b / "f.txt").exists())
        self.assertEqual(list((self.tmp / "state").glob("*.json")), [])
        self.assertIn("Dry run", _result_text(result))

    def test_first_sync_conflict_on_both_sides_different(self):
        self._write(self.a, "f.txt", "A\n")
        self._write(self.b, "f.txt", "B\n")
        result = self._sync()
        self.assertFalse(result.verified)
        self.assertIn("CONFLICT", _result_text(result))
        self.assertEqual((self.a / "f.txt").read_text(), "A\n")
        self.assertEqual((self.b / "f.txt").read_text(), "B\n")


class RefusalTests(_TwoWayTestCase):
    def test_remote_endpoint_refused(self):
        for bad in ("user@host:/x", "https://example.com/share",
                    "ssh://host/path"):
            self.assertIn("local folders only", self._sync(folder_a=bad))

    def test_same_folder_refused(self):
        self.assertIn("same folder", self._sync(folder_a=str(self.a),
                                                folder_b=str(self.a)))

    def test_nested_folders_refused(self):
        sub = self.a / "sub"
        sub.mkdir()
        self.assertIn("INSIDE", self._sync(folder_a=str(self.a),
                                           folder_b=str(sub)))

    def test_missing_folder_refused(self):
        self.assertIn("not found", self._sync(folder_a=str(self.tmp / "nope")))

    def test_missing_second_folder_refused(self):
        self.assertIn("not found", self._sync(folder_b=str(self.tmp / "nope")))

    def test_symlink_skipped_not_followed(self):
        target = self.tmp / "outside.txt"
        target.write_text("outside\n")
        (self.a / "link.txt").symlink_to(target)
        result = self._sync()
        self.assertTrue(result.verified)
        self.assertFalse((self.b / "link.txt").exists())

    def test_cap_hit_reported(self):
        for i in range(5001):
            (self.a / f"f{i:05d}.txt").write_text("x\n")
        result = self._sync()
        self.assertIn("5,000-file cap", _result_text(result))


class StatusTests(_TwoWayTestCase):
    def test_status_with_no_pairs(self):
        self.assertIn("No two-way sync pairs yet", tw.two_way_sync_status())

    def test_status_lists_pair_and_open_conflicts(self):
        self._write(self.a, "f.txt", "v1\n")
        self._sync()
        self._write(self.a, "f.txt", "A\n")
        self._write(self.b, "f.txt", "B\n")
        self._sync()
        text = tw.two_way_sync_status()
        self.assertIn("Two-way sync pairs:", text)
        self.assertIn(str(self.a), text)
        self.assertIn("1 conflict(s) still open", text)
        self.assertIn("f.txt", text)


class RegistrationTests(unittest.TestCase):
    def test_tools_registered(self):
        names = {d["name"] for d in _registry.get_declarations()}
        self.assertIn("sync_folders_two_way", names)
        self.assertIn("two_way_sync_status", names)

    def test_registry_count(self):
        # R9-4 mutagen port: 702 -> 704 (+2).
        self.assertEqual(len(_registry.get_declarations()), 758)  # INTENTIONAL — R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing

    def test_gates(self):
        from core.funnel.tools_gate import (
            _CONFIRM_GATE_TOOLS, _READ_ONLY_TOOLS)
        from core.funnel.tools_gate_egress import _LOCAL_ONLY_READS
        from core.funnel.tools_gate_taint import _UNTRUSTED_PRODUCERS
        from core.funnel.tools_handlers_limits import _TOOL_DEADLINE_CLASS

        self.assertIn("sync_folders_two_way", _CONFIRM_GATE_TOOLS)
        self.assertIn("two_way_sync_status", _READ_ONLY_TOOLS)
        self.assertIn("two_way_sync_status", _LOCAL_ONLY_READS)
        self.assertEqual(
            _UNTRUSTED_PRODUCERS.get("sync_folders_two_way"),
            "filenames")
        self.assertEqual(
            _TOOL_DEADLINE_CLASS.get("sync_folders_two_way"), "long")

    def test_no_mutagen_binary_anywhere(self):
        """PORT, not adopt: no mutagen import, no binary path, no
        subprocess call to a mutagen binary in the module."""
        text = pathlib.Path(tw.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import mutagen", text)
        self.assertNotIn("data/bin/mutagen", text)
        self.assertNotIn("mutagen.exe", text)
        self.assertNotIn("subprocess", text)
        # The SSPL block is documented, not hidden.
        self.assertIn("SSPL", text)
        self.assertIn("HARD-BLOCKED", text)

    def test_catalogue_and_toolnames(self):
        # Read as text from the single catalogue source
        # (core/catalogue_groups_data.py, GAPS-23 F-V7) — the
        # names are checked against the file text instead
        # (the R8-4 precedent).
        root = pathlib.Path(tw.__file__).resolve().parent.parent
        catalog = (root / "core" / "catalogue_groups_data.py").read_text(
            encoding="utf-8")
        self.assertIn('"sync_folders_two_way"', catalog)
        self.assertIn('"two_way_sync_status"', catalog)
        names = (root / "core" / "tool_labels.py").read_text(
            encoding="utf-8")
        self.assertIn('"sync_folders_two_way"', names)
        self.assertIn('"two_way_sync_status"', names)
