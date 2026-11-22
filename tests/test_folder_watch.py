"""B-D2 (2026-11-09) watchdog live-folder sense.

Runs against an INJECTED FAKE backend and a FAKE CLOCK — the
settler is deterministic: no sleeping, no real filesystem
watching, no real waiting. The fake models watchdog honestly
where it matters: events arrive on the backend's thread (here
delivered synchronously by fire()), a stopped backend delivers
nothing, and is_alive() can be flipped to False to simulate a
dead observer.

The real-Windows leg — real folder events on his machine —
is a field leg, owed, not this batch. One test below
(test_real_backend_live_events) uses the REAL watchdog
observer on a tmp dir where watchdog is installed (.venv-qt);
on system python3 (no watchdog) it asserts the honest
engine_unavailable instead of skipping.
"""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

from core.desktop import folder_watch as fw
from core.funnel.tools_registry import tools


class _FakeClock:
    """A clock the test drives by hand. Callable, like
    time.monotonic."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _FakeStat:
    """path -> (size, mtime); missing paths raise OSError,
    like os.stat on a gone file."""

    def __init__(self):
        self.files = {}

    def set(self, path, size, mtime=1000.0):
        self.files[path] = (size, mtime)

    def remove(self, path):
        self.files.pop(path, None)

    def __call__(self, path):
        if path not in self.files:
            raise OSError(f"no such file: {path}")
        size, mtime = self.files[path]

        class _St:
            st_size = size
            st_mtime = mtime

        return _St()


class _FakeBackend:
    """A watchdog-shaped backend the test drives by hand."""

    def __init__(self):
        self.alive = True
        self.started = False
        self.stopped = False
        self.handler = None
        self.folder = None
        self.recursive = None

    def start(self, folder, handler, recursive=True):
        self.started = True
        self.stopped = False
        self.folder = folder
        self.handler = handler
        self.recursive = recursive

    def stop(self):
        self.stopped = True
        self.started = False
        self.handler = None

    def is_alive(self):
        return self.alive and self.started and not self.stopped

    def fire(self, kind, path, dest_path=None, is_directory=False):
        # A stopped backend delivers nothing — watchdog
        # wouldn't either.
        if self.handler is not None:
            self.handler(kind, path, dest_path, is_directory)


def _watcher(clock=None, stat=None, settle=1.0):
    """A FolderWatch with a fake backend + fake clock (no
    settle thread — the test drives sweep() directly)."""
    clock = clock or _FakeClock()
    backend = _FakeBackend()
    watch = fw.FolderWatch(settle_seconds=settle, clock=clock,
                           backend_factory=lambda: backend,
                           stat_fn=stat or _FakeStat())
    return watch, backend, clock


class SettlerTests(unittest.TestCase):
    def setUp(self):
        self.clock = _FakeClock()
        self.stat = _FakeStat()
        self.settler = fw.Settler(now=self.clock, stat_fn=self.stat)

    def test_burst_settles_to_one_event(self):
        p = "/w/report.pdf"
        self.stat.set(p, 100)
        for _ in range(5):
            self.settler.note("modified", p)
            self.clock.advance(0.1)
        self.clock.advance(1.0)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "modified")
        self.assertEqual(got[0]["path"], p)
        self.assertTrue(got[0]["stable"])

    def test_created_stays_created_through_burst(self):
        p = "/w/new.txt"
        self.stat.set(p, 10)
        self.settler.note("created", p)
        for _ in range(3):
            self.settler.note("modified", p)
            self.clock.advance(0.1)
        self.clock.advance(1.0)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        # Born in this burst: it did not exist in the settled
        # state before, so the event is a creation, not a modify.
        self.assertEqual(got[0]["kind"], "created")

    def test_create_then_delete_is_net_zero(self):
        p = "/w/temp.tmp"
        self.stat.set(p, 5)
        self.settler.note("created", p)
        self.clock.advance(0.1)
        self.settler.note("deleted", p)
        self.clock.advance(1.5)
        self.assertEqual(self.settler.sweep(), [])
        self.assertEqual(self.settler.pending_count(), 0)

    def test_modify_then_delete_is_one_deleted(self):
        p = "/w/old.txt"
        self.stat.set(p, 50)
        self.settler.note("modified", p)
        self.clock.advance(0.1)
        self.settler.note("deleted", p)
        self.clock.advance(1.5)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "deleted")
        self.assertEqual(got[0]["path"], p)

    def test_move_is_one_moved_event(self):
        src, dst = "/w/a.txt", "/w/b.txt"
        self.stat.set(src, 40)
        self.stat.set(dst, 40)
        self.settler.note("moved", src, dst)
        self.clock.advance(1.5)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "moved")
        self.assertEqual(got[0]["path"], dst)
        self.assertEqual(got[0]["src_path"], src)
        kinds = [e["kind"] for e in got]
        self.assertNotIn("deleted", kinds)
        self.assertNotIn("created", kinds)

    def test_move_then_modify_settles_to_one_moved(self):
        src, dst = "/w/a.txt", "/w/b.txt"
        self.stat.set(src, 40)
        self.stat.set(dst, 40)
        self.settler.note("moved", src, dst)
        self.settler.note("modified", dst)
        self.clock.advance(0.1)
        self.settler.note("modified", dst)
        self.clock.advance(1.5)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "moved")
        self.assertEqual(got[0]["path"], dst)

    def test_still_changing_file_is_not_stable(self):
        p = "/w/growing.bin"
        self.stat.set(p, 100)
        self.settler.note("modified", p)
        self.clock.advance(1.5)
        # Still growing: the size moved since the last sighting.
        self.stat.set(p, 200)
        self.assertEqual(self.settler.sweep(), [])
        # Quiet now: one more window of silence settles it.
        self.clock.advance(1.5)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "modified")
        self.assertTrue(got[0]["stable"])

    def test_stable_file_is_stable(self):
        p = "/w/done.txt"
        self.stat.set(p, 77)
        self.settler.note("modified", p)
        self.clock.advance(1.5)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["stable"])
        self.assertEqual(got[0]["size"], 77)

    def test_lost_delete_is_reported_not_silent(self):
        p = "/w/vanished.txt"
        self.stat.set(p, 30)
        self.settler.note("modified", p)
        self.clock.advance(1.5)
        # The file is gone and no delete event arrived (the
        # event was lost): silence would hide a real change.
        self.stat.remove(p)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "deleted")

    def test_unknown_baseline_waits_one_more_window(self):
        p = "/w/late.txt"
        # No stat available when the event arrived (the file
        # could not be statted yet): the first sweep must take
        # the reading as the baseline, not claim stability from
        # a single sighting.
        self.settler.note("created", p)
        self.clock.advance(1.5)
        self.stat.set(p, 12)
        self.assertEqual(self.settler.sweep(), [])
        self.clock.advance(1.5)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["kind"], "created")

    def test_directory_settles_on_quiet_time(self):
        d = "/w/newfolder"
        self.settler.note("created", d, is_directory=True)
        self.clock.advance(1.5)
        got = self.settler.sweep()
        self.assertEqual(len(got), 1)
        self.assertTrue(got[0]["is_directory"])

    def test_directory_modified_is_dropped(self):
        # A directory's modification is implied by the child
        # event that caused it — the settler drops it, so a
        # landing file is never double-reported.
        d = "/w/sub"
        self.settler.note("modified", d, is_directory=True)
        self.assertEqual(self.settler.pending_count(), 0)
        self.clock.advance(1.5)
        self.assertEqual(self.settler.sweep(), [])

    def test_unknown_kind_ignored(self):
        self.settler.note("renamed", "/w/x.txt")
        self.assertEqual(self.settler.pending_count(), 0)

    def test_not_yet_quiet_is_not_settled(self):
        p = "/w/fresh.txt"
        self.stat.set(p, 1)
        self.settler.note("created", p)
        self.clock.advance(0.5)
        self.assertEqual(self.settler.sweep(), [])
        self.assertEqual(self.settler.pending_count(), 1)


class FolderWatchTests(unittest.TestCase):
    def test_register_unregister_list(self):
        watch, backend, clock = _watcher()
        with tempfile.TemporaryDirectory() as d:
            got = watch.register(d)
            self.assertTrue(got["ok"], got)
            self.assertEqual(got["folder"], os.path.realpath(d))
            self.assertTrue(backend.started)
            # A second register of the same folder is an honest
            # failure, not a second observer.
            dup = watch.register(d)
            self.assertFalse(dup["ok"])
            self.assertIn("already_watched", dup["reason"])
            listed = watch.list_watches()
            self.assertTrue(listed["ok"])
            self.assertEqual(len(listed["watches"]), 1)
            self.assertTrue(listed["watches"][0]["alive"])
            self.assertTrue(watch.health()["ok"])
            gone = watch.unregister(d)
            self.assertTrue(gone["ok"], gone)
            self.assertTrue(backend.stopped)
            again = watch.unregister(d)
            self.assertFalse(again["ok"])
            self.assertIn("not_watched", again["reason"])
        watch.close()

    def test_register_missing_folder_fails(self):
        watch, _, _ = _watcher()
        got = watch.register("/no/such/folder/anywhere")
        self.assertFalse(got["ok"])
        self.assertIn("no_match", got["reason"])
        watch.close()

    def test_register_file_not_folder_fails(self):
        watch, _, _ = _watcher()
        with tempfile.NamedTemporaryFile() as f:
            got = watch.register(f.name)
            self.assertFalse(got["ok"])
            self.assertIn("not an existing folder", got["reason"])
        watch.close()

    def test_unregistered_folder_produces_no_events(self):
        watch, backend, clock = _watcher()
        with tempfile.TemporaryDirectory() as d:
            watch.register(d)
            p = os.path.join(d, "a.txt")
            backend.fire("created", p)
            # Unregister abandons the unsettled event…
            watch.unregister(d)
            clock.advance(2.0)
            watch.sweep()
            self.assertEqual(watch.drain()["events"], [])
            # …and a stopped backend delivers nothing more.
            backend.fire("created", p)
            clock.advance(2.0)
            watch.sweep()
            self.assertEqual(watch.drain()["events"], [])
        watch.close()

    def test_dead_watch_reports_dead(self):
        watch, backend, clock = _watcher()
        with tempfile.TemporaryDirectory() as d:
            watch.register(d)
            backend.alive = False  # the observer thread died
            health = watch.health()
            self.assertFalse(health["ok"])
            self.assertFalse(health["alive"])
            self.assertIn("dead", health["reason"])
            listed = watch.list_watches()
            self.assertFalse(listed["watches"][0]["alive"])
        watch.close()

    def test_deleted_folder_reports_dead(self):
        watch, _, _ = _watcher()
        d = tempfile.mkdtemp()
        watch.register(d)
        shutil.rmtree(d)  # the watched folder is gone
        health = watch.health()
        self.assertFalse(health["ok"])
        self.assertIn("no longer exists", health["reason"])
        watch.close()

    def test_drain_returns_settled_oldest_first(self):
        watch, backend, clock = _watcher()
        with tempfile.TemporaryDirectory() as d:
            watch.register(d)
            p1 = os.path.join(d, "first.txt")
            p2 = os.path.join(d, "second.txt")
            backend.fire("created", p1)
            clock.advance(0.2)
            backend.fire("created", p2)
            clock.advance(2.0)
            watch.sweep()
            events = watch.drain()["events"]
            self.assertEqual([e["path"] for e in events], [p1, p2])
            self.assertTrue(all(e["stable"] for e in events))
            # A second drain is an honest empty list, not an error.
            self.assertEqual(watch.drain()["events"], [])
        watch.close()

    def test_close_stops_everything(self):
        watch, backend, _ = _watcher()
        with tempfile.TemporaryDirectory() as d:
            watch.register(d)
            self.assertTrue(watch.close()["ok"])
            self.assertTrue(backend.stopped)
            after = watch.register(d)
            self.assertFalse(after["ok"])
            self.assertIn("closed", after["reason"])
        # Idempotent.
        self.assertTrue(watch.close()["ok"])

    def test_settle_thread_is_a_daemon(self):
        # With the real clock the wrapper starts its own settle
        # thread; it must be a daemon that sleeps between sweeps
        # — never a busy loop, never a thread that blocks the
        # caller or the process exit.
        watch = fw.FolderWatch(backend_factory=_FakeBackend)
        with tempfile.TemporaryDirectory() as d:
            watch.register(d)
            thread = watch._settle_thread
            self.assertIsNotNone(thread)
            self.assertTrue(thread.daemon)
            self.assertTrue(thread.is_alive())
        watch.close()

    def test_no_new_tool_registered(self):
        # B-D2 adds NO tool: folder sense is driver-called
        # machinery (like U11's CourseRegistry). Registry stays
        # 665 and schema-lint is 1498 (R7-5 kovrel's pins).
        decls = tools.get_declarations()
        self.assertEqual(len(decls), 769)  # INTENTIONAL — 677 -> 680: semantic_search_knowledge, embed_knowledge, knowledge_embedding_status (R8-1 fastembed port, 2026-11-10).; R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text added; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169
        names = [d["name"] for d in decls]
        self.assertNotIn("folder_watch", names)
        # The only folder-watch tool is the pre-existing
        # folder_watch_cook (armed recipes, run on demand —
        # core/skills_files5_watch_blur_notes.py:179), which is
        # NOT a live watch and is untouched by this batch.
        self.assertEqual([n for n in names if "watch" in n
                          and "folder" in n], ["folder_watch_cook"])
        total = 0
        for decl in decls:
            props = (decl.get("parameters") or {}).get("properties") or {}
            total += len(props)
        self.assertEqual(total, 2121)  # INTENTIONAL — 1513 -> 1530: score_intervention adds 11 properties (candidate_text, kind, confidence, evidence_samples, explicit_request, routine_due, dismissed_recently, user_mid_task, watcher_kind, prediction_available, threshold), run_proactivity_bench adds 1 property (threshold), list_proactivity_scores adds 1 property (limit), proactivity_bench_results adds 1 property (limit), R7-8 proactive-agent 2026-11-10.; R8-1 fastembed port (2026-11-10): 1527 -> 1530 — semantic_search_knowledge adds 2 properties (query, limit), embed_knowledge adds 1 property (limit), knowledge_embedding_status adds 0; R8-3 sumy port (2026-11-10): 1530 -> 1532 — summarise_text adds 2 properties (text, max_sentences); R8-4 screen-break port (2026-11-10): 1532 -> 1538 — eye_rest_timer adds 3 properties (action, interval_minutes, snooze_minutes), breathing_guide adds 2 properties (pattern, cycles), log_glass adds 1 property (action); R8-5 magic-wormhole port (2026-11-10): 1538 -> 1544 — wormhole_offer adds 3 properties (action, file, expiry_minutes), wormhole_receive adds 3 properties (phrase, sender, destination), wormhole_status adds 0 properties; R8-6 yasb port (2026-11-10): 1544 -> 1545 — status_strip adds 1 property (action); R8-7 basic-pitch port (2026-11-10): 1545 -> 1551 — audio_to_midi adds 6 properties (audio_path, output, onset_threshold, frame_threshold, min_note_length_ms, tempo_bpm); audio_to_midi_status adds 0; R8-8 mftool port (2026-11-10): 1551 -> 1557 — fund_returns adds 5 properties (start_value, end_value, years, cashflows, currency), mutual_fund_nav adds 1 property (scheme); R8-9 pypresence adoption (2026-11-10): 1557 -> 1563 — discord_presence adds 5 properties (action, details, state, name, client_id); set_discord_client_id adds 1 property (client_id); R9-2 rustic adoption (2026-11-10): 1563 -> 1572 — init_backup_repo adds 1 property (repo_path); backup_files adds 3 (sources, repo_path, label); list_backup_snapshots adds 1 (repo_path); restore_backup adds 3 (snapshot, destination, repo_path); backup_status adds 1 (repo_path); R9-3 whoosh port (2026-11-10): 1572 -> 1576 — index_files adds 2 properties (folders, rebuild), search_indexed_files adds 2 properties (query, limit), disk_index_status adds 0; R9-4 mutagen port (2026-11-10): 1576 -> 1580 — sync_folders_two_way adds 4 properties (folder_a, folder_b, mirror_delete, dry_run); two_way_sync_status adds none; R9-8 elodie port (2026-11-10): 1580 -> 1585 — organise_photos adds 3 properties (source, destination, dry_run); preview_photo_organisation adds 2 properties (source, destination); R10-1 chrome-devtools-mcp port (2026-11-10): 1585 -> 1591 — browser_console adds 2 properties (seconds, target), browser_network adds 3 (seconds, target, max_entries), browser_performance adds 1 (target); R10-3 browser-history port (2026-11-10): 1591 -> 1598 — browser_history adds 4 properties (profile_dir, browser, days, limit), browser_bookmarks adds 3 (profile_dir, browser, limit); R10-4 python-adblock port (2026-11-10): 1598 -> 1603 — check_url_blocked adds 4 properties (url, list_file, source_domain, resource_type), filter_list_status adds 1 (list_file); R10-5 python-readability port (2026-11-10): 1603 -> 1605 — extract_article adds 2 properties (url, html); R11-1 ollama port (2026-11-10): 1605 -> 1607 — ollama_pull + ollama_delete add 1 property each (model); ollama_status adds 0; R16-8 event-planning pack (2026-11-11): 1607 -> 1640 — event_plan 7 + event_guest 7 + event_vendor 5 + event_budget 5 + event_milestone 5 + event_schedule 4 properties; R16-9 attic pack (2026-11-11): 1640 -> 1650 — home_inventory adds 10 properties (action, item, box, room, condition, value, purchased, photo, detail, quantity); R16-10 GreaterWMS pack (2026-11-11): 1650 -> 1658 — warehouse_stock adds 8 properties (action, item, location, from_location, to_location, quantity, capacity, detail); R17-1 bookrail pack (2026-11-11): 1658 -> 1674 — bookings adds 16 properties (action, resource, start, end, customer, capacity, timezone, schedule, buffer_minutes, booking_window_days, cancel_hours, hold_minutes, price, idempotency_key, booking_id, detail); R17-2 trivy pack (2026-11-11): 1674 -> 1679 — security_scan adds 5 properties (action, package, version, path, limit); R17-3 go2rtc pack (2026-11-11): 1679 -> 1690 — ip_camera adds 11 properties (action, camera, url, snapshot_url, kind, username, password, duration_seconds, path, limit, detail); R17-4 MCSManager pack (2026-11-11): 1690 -> 1702 — game_server adds 12 properties (action, server, kind, command, working_dir, host, port, rcon_host, rcon_port, rcon_password, lines, timeout_seconds); R17-5 komga pack (2026-11-11): 1702 -> 1713 — ebook_library adds 11 properties (action, path, book, author, percentage, page, progress, device, device_id, limit, port); R17-6 openstays pack (2026-11-11): 1713 -> 1735 — lodging adds 22 properties (action, property, unit, guest, contact, check_in, check_out, stay_id, date, state, cents, min_nights, sleeps, type, timezone, currency, tax_rate_bps, turnover_nights, deposit, cancel_policy, limit, detail); R17-7 qlcplus pack (2026-11-12): 1735 -> 1750 — stage_lighting adds 15 properties (action, mode, node, host, protocol, universe, address, fixture, type, channels, scene, chase, steps, levels, limit); R17-8 pvlib pack (2026-11-12): 1750 -> 1769 — solar_yield adds 19 properties (action, latitude, longitude, date, time, tilt, azimuth, altitude, linke_turbidity, albedo, temp_air, pdc0, gamma_pdc, noct, inverter_ac0, eta_inv_nom, start_date, end_date, detail); R17-9 wardrobe pack (2026-11-12): 1769 -> 1788 — wardrobe adds 19 properties (action, mode, name, category, colour, seasons, warmth, formality, photo, garment_id, garment_ids, outfit_id, schedule_id, date, temp_c, condition, note, limit, detail); R18-1 stripe pack (2026-11-12): 1788 -> 1812 — billing adds 24 properties (action, mode, customer_id, name, email, price_id, quantity, amount, subscription_id, payment_link_id, invoice_id, payment_intent_id, charge_id, reason, approval_ticket, payload, signature, webhook_secret, api_key, success_url, cancel_url, checkout_mode, limit, detail); R18-3 mcp-context-forge pack (2026-11-12): 1812 -> 1828 — mcp_registry adds 16 properties; R18-4 a2a pack (2026-11-12): 1828 -> 1841 — agent_call adds 13 properties; R18-5 dspy pack (2026-11-12): 1841 -> 1855 — prompt_tune adds 14 properties; R18-6 fonoster pack (2026-11-12): 1855 -> 1869 — voice_agent adds 14 properties; R18-7 attendize pack (2026-11-12): 1869 -> 1884 — event_tickets adds 15 properties; R18-8 ta pack (2026-11-12): 1884 -> 1893 — technical_analysis adds 9 properties; R18-10 timetrace pack (2026-11-12): 1893 -> 1906 — time_tracker adds 13 properties; R19-1 videolingo pack (2026-11-12): 1906 -> 1919 — audio_localise adds 13 properties; R19-2 ortools pack (2026-11-12): 1919 -> 1938 — optimise adds 19 properties; R19-3 aureuserp pack (2026-11-12): 1938 -> 1961 — erp adds 23 properties; R19-4 vidbee pack (2026-11-12): 1961 -> 1971 — media_chat adds 10 properties; R19-5 invoify pack (2026-11-12): 1971 -> 1987 — invoice adds 15 properties + send_email gains attachments; R19-6 tolgee pack (2026-11-12): 1987 -> 2002 — translations adds 15 properties; R19-7 spoolman pack (2026-11-12): 2002 -> 2024 — filament adds 22 properties; R19-8 djangocrm pack (2026-11-12): 2024 -> 2045 — deals adds 21 properties; R19-10 vroom pack (2026-11-12): 2045 -> 2053 — vehicle_routes adds 8 properties; GAPS-7 connectors pack (2026-11-13): 2053 -> 2055 — sync_updates adds 2 properties; SOCIAL Phase 1 (2026-11-13): 2055 -> 2060 — social_send adds 5 properties; GAPS-8 scheduled-agents pack (2026-11-13): 2060 -> 2063 — scheduled_agents adds 3 properties; GAPS-20 loopback-meeting pack (2026-11-13): 2063 -> 2064 — start_meeting gains system_audio; Click-anywhere (2026-11-14): 2064 -> 2073 — click_anywhere's 9 properties; RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169; Stage 0 features batch 4 (2026-11-22): census 764/2102/384/380/169 -> 769/2121/384/385/169

    def test_engine_availability_is_honest_per_interpreter(self):
        # watchdog is installed in .venv-qt only (PEP 668 blocks
        # pip on system python3). This asserts the actual state
        # per interpreter — never a skip, never a guess.
        if ".venv-qt" in sys.executable:
            self.assertTrue(fw.engine_available())
            self.assertTrue(fw.engine_status()["available"])
            self.assertEqual(fw.engine_status()["backend"], "watchdog")
        else:
            self.assertFalse(fw.engine_available())
            status = fw.engine_status()
            self.assertFalse(status["available"])
            self.assertIn("watchdog", status["error"])

    def test_real_backend_live_events(self):
        # The one real-backend test: a real watchdog observer
        # on a tmp dir. Where watchdog is installed (.venv-qt)
        # a created file must arrive as ONE settled created
        # event; where it is not (system python3) register()
        # must fail honestly with engine_unavailable.
        watch = fw.FolderWatch(settle_seconds=0.2)
        try:
            with tempfile.TemporaryDirectory() as d:
                got = watch.register(d)
                if not fw.engine_available():
                    self.assertFalse(got["ok"])
                    self.assertIn("engine_unavailable", got["reason"])
                    return
                self.assertTrue(got["ok"], got)
                p = os.path.join(d, "landed.txt")
                with open(p, "w") as f:
                    f.write("hello")
                deadline = time.monotonic() + 5.0
                events = []
                while time.monotonic() < deadline:
                    time.sleep(0.1)
                    events = watch.drain()["events"]
                    if events:
                        break
                self.assertEqual(len(events), 1, events)
                self.assertEqual(events[0]["kind"], "created")
                self.assertEqual(events[0]["path"], p)
                self.assertTrue(events[0]["stable"])
        finally:
            watch.close()


if __name__ == "__main__":
    unittest.main()
