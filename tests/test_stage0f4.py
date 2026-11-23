"""Stage 0 features batch 4 (Group G, 2026-11-22): notes, lists
and goals — items 17 (notes tool — missing entirely), 28
(wishlist — missing entirely), 29 (movie/show watchlist —
missing entirely), 35 (general goal tracker — missing
entirely), 32 (voice memos — PARTIAL: recording existed, list
+ play-back did not). Plus the owner-gate extension (parent
ruling, batch 3): budget_envelope report and price_watch
list/check reads join the L-24 owner gate (portfolio_tracker
list/total was already gated by batch 3 — pinned here as
still holding).

This suite pins the batch, so it can never regress silently:

- All five tools are registered, classified (effectful, FREE by
  design, app-state), labelled, grouped (Everyday skills),
  synonymed, and in the evidence table + snapshot with class
  free. None is a read or confirm-gated.
- All five are owner-only by construction (L-24): they join
  _OWNER_ONLY_DISPATCHED, and _is_read_action names the read
  half (notes list/read; wishlist/watchlist/goal_tracker list;
  voice_memos list/play/stop) — so a private list is refused in
  an unproven live-origin session (the funnel leg lives in the
  repro probe; this suite pins the gate sets the funnel reads).
- The gate extension: budget_envelope report and price_watch
  list/check are owner-only reads too (their write actions stay
  non-read); portfolio_tracker list/total still gates.
- Notes take/list/read/remove over memory facts (one fact per
  note, keyed by timestamp); wishlist add/list/remove;
  watchlist add/list/mark_watched/remove with kind movie/show;
  goal_tracker add/list/update/remove with absolute progress
  (a repeated update cannot double-count) and a complete line
  at/over target. Every write re-reads before the claim
  (A1-store); broken facts are counted, never raised.
- Voice memos list the memos in ~/Music/Nova (newest first,
  date + transcript excerpt) and play one through the audio
  library's play seam in a daemon thread (a memo already
  playing is stopped first); without the audio library the play
  refuses honestly, never a fake success.
- Census pins: 769 tools / 384 read / 385 effectful / 169
  confirm-gated / params 2121 / grouped 766 / closed 768 /
  already-verified 319 / §2c rows 385 / labels 694 /
  productivity 152 / media_audio 44 / desktop offered 76 /
  assistant offered 73.

Stdlib unittest only. All list-store writes go through a temp
MemoryStore + temp DATA_DIR (the _store seam); all memo files
go through a temp memo dir (the _memo_folder seam); playback
goes through a stubbed _start_memo_player. No data/ writes,
no network, no real audio.
"""

import json
import re
import struct
import tempfile
import unittest
import wave
from pathlib import Path

import core.skills_notes as _notes  # noqa: E402
import core.skills_senses_checks as _checks  # noqa: E402
import core.skills_money_budget as _budget  # noqa: E402
import core.skills_web6_watch as _watch  # noqa: E402
from core.funnel import tools_gate as _gate  # noqa: E402
from core.funnel import toolbox_tokens as _tokens  # noqa: E402
from core.funnel.tool_classification import (  # noqa: E402
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
    _is_read_action,
)
from core.funnel.tool_profiles import DESKTOP_TOOL_PRIORITY  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core import tool_labels as _labels  # noqa: E402
from core.catalogue_groups_data import GROUP_MEMBERS  # noqa: E402

_TREE = Path(__file__).resolve().parent.parent
_NEW = ("notes", "wishlist", "watchlist", "goal_tracker",
        "voice_memos")


class _TempLists:
    """Temp MemoryStore + temp DATA_DIR + temp memo dir for the
    list legs. Restores every seam on exit."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.memo_dir = self.dir / "memos"
        self.memo_dir.mkdir()
        from core.memory.memory import MemoryStore  # noqa: E402
        self.store = MemoryStore(self.dir / "assistant.db")
        import core.skills_money_helpers as helpers  # noqa: E402
        import core.config as config_mod  # noqa: E402
        self._old = (helpers._store, _notes._store,
                     config_mod.DATA_DIR,
                     _checks._memo_folder,
                     _checks._start_memo_player,
                     _checks._audio_library_available)
        helpers._store = lambda: self.store
        _notes._store = lambda: self.store
        config_mod.DATA_DIR = self.dir
        _checks._memo_folder = lambda: self.memo_dir
        self.played = []
        _checks._start_memo_player = self._record_play
        _checks._audio_library_available = lambda: True
        return self

    def _record_play(self, samples, rate, stop_event):
        self.played.append((samples, rate))
        return None

    def __exit__(self, *exc):
        import core.skills_money_helpers as helpers  # noqa: E402
        import core.config as config_mod  # noqa: E402
        (helpers._store, _notes._store,
         config_mod.DATA_DIR,
         _checks._memo_folder,
         _checks._start_memo_player,
         _checks._audio_library_available) = self._old
        self.tmp.cleanup()
        return False

    def memo(self, slug, stamp, transcript=None):
        """A memo WAV + transcript in the temp memo dir."""
        wav_path = self.memo_dir / f"{slug}-{stamp}.wav"
        frames = struct.pack("<8000h", *([0] * 8000))
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(frames)
        txt = wav_path.with_suffix(".txt")
        if transcript is None:
            txt.write_text(
                f"{slug.replace('-', ' ')} — 22 Nov 2026, 14:30\n\n"
                "(Transcription wasn't available on this PC — "
                "the recording itself is saved as audio next "
                "to this file.)\n", encoding="utf-8")
        else:
            txt.write_text(
                f"{slug.replace('-', ' ')} — 22 Nov 2026, 14:30\n\n"
                f"{transcript}\n", encoding="utf-8")
        return wav_path


class TestRegistration(unittest.TestCase):
    """All five tools registered, classified, labelled, grouped."""

    @classmethod
    def setUpClass(cls):
        cls.decls = {d["name"]: d for d in tools.get_declarations()}

    def test_all_five_registered_with_handlers(self):
        for name in _NEW:
            with self.subTest(tool=name):
                self.assertIn(name, self.decls)
        self.assertTrue(callable(_notes.notes))
        self.assertTrue(callable(_notes.wishlist))
        self.assertTrue(callable(_notes.watchlist))
        self.assertTrue(callable(_notes.goal_tracker))
        self.assertTrue(callable(_checks.voice_memos))

    def test_all_five_free_app_state_not_read_not_confirm(self):
        for name in _NEW:
            with self.subTest(tool=name):
                self.assertEqual(_FREE_BY_DESIGN.get(name), "app-state")
                self.assertNotIn(name, _READ_ONLY_TOOLS)
                self.assertNotIn(name, _CONFIRM_GATE_TOOLS)

    def test_all_five_owner_only_dispatched(self):
        for name in _NEW:
            self.assertIn(name, _gate._OWNER_ONLY_DISPATCHED)

    def test_is_read_action_branches(self):
        self.assertTrue(_is_read_action("notes", {"action": "list"}))
        self.assertTrue(_is_read_action("notes", {"action": "read"}))
        self.assertFalse(_is_read_action("notes", {"action": "add"}))
        self.assertFalse(_is_read_action("notes", {"action": "remove"}))
        for name in ("wishlist", "watchlist", "goal_tracker"):
            with self.subTest(tool=name):
                self.assertTrue(_is_read_action(name, {"action": "list"}))
                self.assertFalse(_is_read_action(name, {"action": "add"}))
        self.assertTrue(_is_read_action("voice_memos", {"action": "list"}))
        self.assertTrue(_is_read_action("voice_memos", {"action": "play"}))
        self.assertTrue(_is_read_action("voice_memos", {"action": "stop"}))
        self.assertFalse(_is_read_action("voice_memos", {"action": "x"}))

    def test_desktop_priority_64_all_resident(self):
        self.assertEqual(len(DESKTOP_TOOL_PRIORITY), 64)
        for name in _NEW:
            self.assertIn(name, DESKTOP_TOOL_PRIORITY)

    def test_synonyms_labels_groups(self):
        for name in _NEW:
            with self.subTest(tool=name):
                self.assertIn(name, _tokens.TOOL_SYNONYMS)
                self.assertIn(name, _labels.TOOL_FRIENDLY_NAMES)
                self.assertIn(name, GROUP_MEMBERS["Everyday skills"])

    def test_properties(self):
        self.assertEqual(
            set(self.decls["notes"]["parameters"]["properties"]),
            {"action", "text", "title", "index"})
        self.assertEqual(
            set(self.decls["wishlist"]["parameters"]["properties"]),
            {"action", "item", "price", "note"})
        self.assertEqual(
            set(self.decls["watchlist"]["parameters"]["properties"]),
            {"action", "title", "kind", "note"})
        self.assertEqual(
            set(self.decls["goal_tracker"]["parameters"]["properties"]),
            {"action", "name", "target", "unit", "progress"})
        self.assertEqual(
            set(self.decls["voice_memos"]["parameters"]["properties"]),
            {"action", "index"})

    def test_evidence_free_snapshot_app_state(self):
        ev = json.loads(
            (_TREE / "tests" / "_classification_evidence.json")
            .read_text(encoding="utf-8"))
        snap = json.loads(
            (_TREE / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        for name in _NEW:
            with self.subTest(tool=name):
                self.assertEqual(ev[name]["class"], "free")
                self.assertEqual(
                    snap[name],
                    {"read": False, "confirm": False,
                     "free": "app-state"})

    def test_section_2c_rows(self):
        doc = (_TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        section = doc[doc.index("## 2c."):doc.index("## 3.")]
        for name in _NEW:
            self.assertIn(f"| `{name}` | A1-store", section)


class TestOwnerGateExtension(unittest.TestCase):
    """The gate extension (parent ruling, batch 3)."""

    def test_budget_and_price_watch_join_dispatched(self):
        for name in ("budget_envelope", "price_watch"):
            self.assertIn(name, _gate._OWNER_ONLY_DISPATCHED)

    def test_budget_report_is_read_set_is_not(self):
        self.assertTrue(_is_read_action("budget_envelope",
                                        {"action": "report"}))
        self.assertFalse(_is_read_action("budget_envelope",
                                         {"action": "set"}))
        self.assertFalse(_is_read_action("budget_envelope",
                                         {"action": "spend"}))

    def test_price_watch_list_check_are_reads_add_is_not(self):
        self.assertTrue(_is_read_action("price_watch",
                                        {"action": "list"}))
        self.assertTrue(_is_read_action("price_watch",
                                        {"action": "check"}))
        self.assertFalse(_is_read_action("price_watch",
                                         {"action": "add"}))
        self.assertFalse(_is_read_action("price_watch",
                                         {"action": "remove"}))

    def test_portfolio_gate_still_holds(self):
        self.assertIn("portfolio_tracker", _gate._OWNER_ONLY_DISPATCHED)
        self.assertTrue(_is_read_action("portfolio_tracker",
                                        {"action": "list"}))
        self.assertTrue(_is_read_action("portfolio_tracker",
                                        {"action": "total"}))
        self.assertFalse(_is_read_action("portfolio_tracker",
                                         {"action": "add"}))


class TestNotes(unittest.TestCase):
    """Item 17: take a note, list, read one back, remove one."""

    def test_empty_list_names_the_add(self):
        with _TempLists():
            out = _notes.notes(action="list")
            self.assertIn("No notes yet", out)
            self.assertIn("action 'add'", out)

    def test_add_list_read_remove_round_trip(self):
        with _TempLists() as tmp:
            added = _notes.notes(action="add", text="Buy milk tomorrow",
                                 title="Shopping")
            self.assertIn("Noted: 'Shopping'", added)
            listed = _notes.notes(action="list")
            self.assertIn("Notes (1):", listed)
            self.assertIn("1. Shopping", listed)
            read = _notes.notes(action="read", index=1)
            self.assertIn("Buy milk tomorrow", read)
            self.assertIn("Shopping", read)
            removed = _notes.notes(action="remove", index=1)
            self.assertIn("Removed note 1", removed)
            self.assertIn("No notes yet", _notes.notes(action="list"))
            self.assertEqual(
                [f for f in tmp.store.get_all_facts()
                 if f["category"] == "notes"], [])

    def test_title_defaults_to_first_line(self):
        with _TempLists():
            _notes.notes(action="add", text="Call the bank about the card")
            listed = _notes.notes(action="list")
            self.assertIn("Call the bank about the card", listed)

    def test_newest_first(self):
        with _TempLists():
            _notes.notes(action="add", text="first note")
            _notes.notes(action="add", text="second note")
            listed = _notes.notes(action="list")
            self.assertLess(listed.index("second note"),
                            listed.index("first note"))

    def test_bad_index_and_bad_action(self):
        with _TempLists():
            _notes.notes(action="add", text="only note")
            self.assertIn("out of range",
                          _notes.notes(action="read", index=2))
            self.assertIn("number from the list",
                          _notes.notes(action="read", index="x"))
            self.assertIn("Note actions are",
                          _notes.notes(action="shred"))
            self.assertIn("needs the note's text",
                          _notes.notes(action="add", text="   "))

    def test_broken_fact_counted_never_raised(self):
        with _TempLists() as tmp:
            tmp.store.save_fact("notes", "20260101-000000",
                                "{not json")
            entries, broken = _notes._load_notes()
            self.assertEqual(broken, 1)
            self.assertEqual(entries, [])
            self.assertIn("couldn't be read",
                          _notes.notes(action="list"))


class TestWishlist(unittest.TestCase):
    """Item 28: things the user wants."""

    def test_add_list_remove_round_trip(self):
        with _TempLists() as tmp:
            added = _notes.wishlist(action="add", item="Headphones",
                                    price=80, note="wireless")
            self.assertIn("Added 'Headphones'", added)
            self.assertIn("Price noted: 80", added)
            listed = _notes.wishlist(action="list")
            self.assertIn("Wishlist (1):", listed)
            self.assertIn("Headphones — 80 (wireless)", listed)
            removed = _notes.wishlist(action="remove", item="headphones")
            self.assertIn("Removed 'Headphones'", removed)
            self.assertIn("wishlist is empty",
                          _notes.wishlist(action="list"))
            self.assertEqual(
                [f for f in tmp.store.get_all_facts()
                 if f["category"] == "wishlist"], [])

    def test_duplicate_add_refused_remove_unknown_names_known(self):
        with _TempLists():
            _notes.wishlist(action="add", item="Lamp")
            self.assertIn("already on your wishlist",
                          _notes.wishlist(action="add", item="lamp"))
            missing = _notes.wishlist(action="remove", item="Chair")
            self.assertIn("isn't on your wishlist", missing)
            self.assertIn("Lamp", missing)

    def test_empty_list_and_bad_action(self):
        with _TempLists():
            self.assertIn("wishlist is empty",
                          _notes.wishlist(action="list"))
            self.assertIn("Wishlist actions are",
                          _notes.wishlist(action="buy"))
            self.assertIn("needs the thing you want",
                          _notes.wishlist(action="add", item=""))


class TestWatchlist(unittest.TestCase):
    """Item 29: films and shows to watch."""

    def test_add_list_mark_watched_remove_round_trip(self):
        with _TempLists() as tmp:
            _notes.watchlist(action="add", title="Dune", kind="movie")
            _notes.watchlist(action="add", title="Severance",
                             kind="show", note="sci-fi")
            listed = _notes.watchlist(action="list")
            self.assertIn("Watchlist (2):", listed)
            self.assertIn("Dune (movie)", listed)
            self.assertIn("Severance (show) — sci-fi", listed)
            marked = _notes.watchlist(action="mark_watched",
                                      title="dune")
            self.assertIn("Marked 'Dune' as watched", marked)
            listed = _notes.watchlist(action="list")
            self.assertIn("Watched:", listed)
            self.assertIn("Dune (movie) (watched ", listed)
            removed = _notes.watchlist(action="remove", title="Dune")
            self.assertIn("Removed 'Dune'", removed)
            self.assertEqual(
                len([f for f in tmp.store.get_all_facts()
                     if f["category"] == "watchlist"]), 1)

    def test_bad_kind_and_already_watched(self):
        with _TempLists():
            self.assertIn("kind is 'movie' or 'show'",
                          _notes.watchlist(action="add", title="X",
                                           kind="book"))
            _notes.watchlist(action="add", title="Dune", kind="movie")
            _notes.watchlist(action="mark_watched", title="Dune")
            self.assertIn("already marked as watched",
                          _notes.watchlist(action="mark_watched",
                                           title="Dune"))
            self.assertIn("isn't on your watchlist",
                          _notes.watchlist(action="remove",
                                           title="Unknown"))

    def test_empty_list_and_bad_action(self):
        with _TempLists():
            self.assertIn("watchlist is empty",
                          _notes.watchlist(action="list"))
            self.assertIn("Watchlist actions are",
                          _notes.watchlist(action="binge"))


class TestGoalTracker(unittest.TestCase):
    """Item 35: the user's own goals."""

    def test_add_list_update_complete_remove_round_trip(self):
        with _TempLists() as tmp:
            added = _notes.goal_tracker(action="add", name="Read books",
                                        target=12, unit="books")
            self.assertIn("Added the goal 'Read books'", added)
            self.assertIn("target 12 books", added)
            listed = _notes.goal_tracker(action="list")
            self.assertIn("Goals (1):", listed)
            self.assertIn("Read books — 0 books of 12 books (0%)",
                          listed)
            updated = _notes.goal_tracker(action="update",
                                          name="read books", progress=6)
            self.assertIn("6 books of 12 books (50%)", updated)
            done = _notes.goal_tracker(action="update",
                                       name="Read books", progress=12)
            self.assertIn("complete", done)
            self.assertIn("Well done", done)
            removed = _notes.goal_tracker(action="remove",
                                          name="Read books")
            self.assertIn("Removed the goal", removed)
            self.assertEqual(
                [f for f in tmp.store.get_all_facts()
                 if f["category"] == "goals"], [])

    def test_update_is_absolute_not_an_increment(self):
        with _TempLists():
            _notes.goal_tracker(action="add", name="Run", target=100,
                                unit="km")
            _notes.goal_tracker(action="update", name="Run", progress=30)
            _notes.goal_tracker(action="update", name="Run", progress=30)
            listed = _notes.goal_tracker(action="list")
            self.assertIn("30 km of 100 km (30%)", listed)

    def test_bad_target_bad_progress_unknown_goal(self):
        with _TempLists():
            self.assertIn("target more than zero",
                          _notes.goal_tracker(action="add", name="X",
                                              target=0))
            _notes.goal_tracker(action="add", name="Run", target=100)
            self.assertIn("zero or more",
                          _notes.goal_tracker(action="update",
                                              name="Run", progress=-1))
            missing = _notes.goal_tracker(action="update", name="Swim",
                                          progress=5)
            self.assertIn("There's no goal called 'Swim'", missing)
            self.assertIn("Run", missing)

    def test_duplicate_add_points_at_update(self):
        with _TempLists():
            _notes.goal_tracker(action="add", name="Run", target=100)
            self.assertIn("already a goal",
                          _notes.goal_tracker(action="add", name="run",
                                              target=50))


class TestVoiceMemos(unittest.TestCase):
    """Item 32: list + play-back for the recorded memos."""

    def test_empty_list_names_voice_note(self):
        with _TempLists():
            out = _checks.voice_memos(action="list")
            self.assertIn("No voice memos yet", out)
            self.assertIn("voice_note", out)

    def test_list_newest_first_with_excerpt(self):
        with _TempLists() as tmp:
            tmp.memo("shopping-ideas", "20261122-143000",
                     "Buy milk and bread")
            tmp.memo("meeting-notes", "20261121-090000",
                     "Discuss the launch date")
            out = _checks.voice_memos(action="list")
            self.assertIn("Voice memos (2):", out)
            self.assertLess(out.index("shopping ideas"),
                            out.index("meeting notes"))
            self.assertIn("22 Nov 2026, 14:30", out)
            self.assertIn("“Buy milk and bread”", out)

    def test_list_marks_no_transcript_honestly(self):
        with _TempLists() as tmp:
            tmp.memo("quick-thought", "20261122-150000",
                     transcript=None)
            out = _checks.voice_memos(action="list")
            self.assertIn("quick thought", out)
            self.assertIn("no transcript saved", out)

    def test_list_skips_non_wav_files(self):
        with _TempLists() as tmp:
            tmp.memo("real-memo", "20261122-143000", "hello")
            (tmp.memo_dir / "notes.txt").write_text("not a memo",
                                                    encoding="utf-8")
            out = _checks.voice_memos(action="list")
            self.assertIn("Voice memos (1):", out)

    def test_read_wav_returns_real_samples(self):
        with _TempLists() as tmp:
            path = tmp.memo("real-memo", "20261122-143000", "hello")
            samples, rate = _checks._read_memo_wav(path)
            self.assertEqual(rate, 16000)
            self.assertEqual(len(samples), 8000)

    def test_play_starts_player_with_the_memo(self):
        with _TempLists() as tmp:
            tmp.memo("shopping-ideas", "20261122-143000",
                     "Buy milk and bread")
            out = _checks.voice_memos(action="play", index=1)
            self.assertIn("Playing memo 1", out)
            self.assertIn("shopping ideas", out)
            self.assertIn("seconds", out)
            self.assertEqual(len(tmp.played), 1)
            samples, rate = tmp.played[0]
            self.assertEqual(rate, 16000)
            self.assertEqual(len(samples), 8000)

    def test_play_refuses_without_the_audio_library(self):
        with _TempLists() as tmp:
            tmp.memo("shopping-ideas", "20261122-143000",
                     "Buy milk and bread")
            _checks._audio_library_available = lambda: False
            out = _checks.voice_memos(action="play", index=1)
            self.assertIn("audio library isn't available", out)
            self.assertEqual(tmp.played, [])

    def test_play_bad_index_and_stop_when_idle(self):
        with _TempLists() as tmp:
            tmp.memo("shopping-ideas", "20261122-143000",
                     "Buy milk and bread")
            self.assertIn("out of range",
                          _checks.voice_memos(action="play", index=2))
            self.assertIn("number from the list",
                          _checks.voice_memos(action="play",
                                              index="x"))
            self.assertIn("No voice memo is playing",
                          _checks.voice_memos(action="stop"))

    def test_stop_after_play(self):
        with _TempLists() as tmp:
            tmp.memo("shopping-ideas", "20261122-143000",
                     "Buy milk and bread")
            _checks.voice_memos(action="play", index=1)
            out = _checks.voice_memos(action="stop")
            self.assertIn("Stopped playing 'shopping ideas'", out)
            self.assertIn("No voice memo is playing",
                          _checks.voice_memos(action="stop"))

    def test_bad_action(self):
        with _TempLists():
            self.assertIn("Voice memo actions are",
                          _checks.voice_memos(action="record"))


class TestCensus(unittest.TestCase):
    """Census pins for the batch."""

    @classmethod
    def setUpClass(cls):
        cls.decls = {d["name"]: d for d in tools.get_declarations()}
        doc = (_TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
            encoding="utf-8")
        cls.totals = json.loads(re.search(
            r"AUDIT-TOTALS:\s*(\{[^}]*\})", doc).group(1))
        cls.counts = json.loads(re.search(
            r"AUDIT-COUNTS:\s*(\{[^}]*\})", doc).group(1))
        section_2c = doc[doc.index("## 2c."):doc.index("## 3.")]
        cls.rows_2c = re.findall(r"^\| `([a-z0-9_]+)` \|",
                                 section_2c, re.M)

    def test_totals(self):
        self.assertEqual(
            (self.totals["total"], self.totals["read"],
             self.totals["effectful"], self.totals["confirm_gated"],
             self.totals["already_verified_in_handler"]),
            (772, 386, 386, 170, 320))  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_params_grouped_closed(self):
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in self.decls.values())
        grouped = sum(len(v) for v in GROUP_MEMBERS.values())
        closed = sum(
            1 for d in self.decls.values()
            if d.get("parameters", {}).get("additionalProperties")
            is False)
        self.assertEqual(total_params, 2126)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(grouped, 769)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(closed, 771)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170

    def test_section_2c_rows_labels_families(self):
        self.assertEqual(len(self.rows_2c), 386)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(set(self.rows_2c)), 386)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(len(_labels.TOOL_FRIENDLY_NAMES), 697)  #Darwin D1 (2026-11-23): census 769/2121/384/385/169 -> 772/2126/386/386/170
        self.assertEqual(self.counts.get("productivity"), 152)
        self.assertEqual(self.counts.get("media_audio"), 44)

    def test_profiles(self):
        from core.funnel import tool_profiles as profiles  # noqa: E402
        self.assertEqual(
            len(profiles.profile_declarations(tools, "desktop")), 76)
        self.assertEqual(
            len(profiles.profile_declarations(tools, "assistant")), 73)


if __name__ == "__main__":
    unittest.main()
