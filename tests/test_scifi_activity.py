"""Screen 3 Activity — the density test (sci-fi prototype 3 of 3).

The look has to survive contact with real work: Activity as it
exists — four or five jobs on one scroll, about twenty-five
controls. The five density-test checks (deep doc section 5,
Screen 3), as reassigned by the UI rethink Build 2:

1. Five-second headline check — one headline LINE per section,
   the count joined to its label at 20 Sora semibold; everything
   else smaller. (Build 2: the giant 28 mono figure is gone.)
2. Four weights, no fifth — headline line; section headings;
   primary values inside rows; metadata and labels. (Build 2:
   the heading and the row value share the 16 rung — weight,
   not size, separates them.)
3. Rows are instruments — a small label, one reading, one
   hairline; detail one tap away, never on the row's face.
4. The pending row is quiet text — every visible queue is the
   same kind of thing, first queue first; none shouts.
   (Build 2: no primary button anymore.)
5. A real morning stays dense and calm — add a reminder, check
   a routine, approve a pending thing, and the page does not
   shout, flicker, or re-layout under the work.

The first four are asserted here (model + real window). The
fifth is asserted in code where it can be (the morning's
operations leave the figures and the row counts standing) and
judged from the renders for the rest; the live-morning proof
is a field leg on his Windows machine.

Pure tests run on both interpreters. Qt tests need PySide6
(the .venv-qt interpreter); on an interpreter without it they
skip, following the programme's standing optional-dependency
skips.
"""

import datetime
import json
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui import room_activity as ra  # noqa: E402
from gui.room_home import contrast_ratio, luminance  # noqa: E402
from gui import style_tokens as tokens  # noqa: E402

RENDER_DIR = ROOT.parents[2] / "scifi_activity_renders"

PENDING = {
    "step_index": 0,
    "tool": "menu_action",
    "label": "Close tab",
    "sentence": "Menu path refused (C-14 approve-first): "
                "'close tab' needs approval",
    "confirm_arg": "confirmed",
}


def _record(task_id, state, title, steps=1, outcomes=(),
            pending=None, updated_at="2026-11-16T09:00:00"):
    record = {
        "task_id": task_id,
        "title": title,
        "state": state,
        "steps": [{"tool": "menu_action", "label": "Close tab",
                   "args": {"action": "list"}}] * steps,
        "outcomes": list(outcomes),
        "updated_at": updated_at,
    }
    if pending is not None:
        record["pending_approval"] = dict(pending)
    return record


SUCCESS = [{"tool": "menu_action", "label": "Close tab",
            "status": "success"}]
FAILED = [{"tool": "menu_action", "label": "Close tab",
           "status": "failed"}]


def _dense_records():
    """Seven records: 4 live, 3 finished, 2 needing attention."""
    return [
        _record("t1", "running", "Sync the project folder",
                steps=2, outcomes=SUCCESS,
                updated_at="2026-11-16T10:00:00"),
        _record("t2", "running", "Back up the photo library",
                steps=3, updated_at="2026-11-16T09:30:00"),
        _record("t3", "paused", "Close the browser tab",
                pending=PENDING, updated_at="2026-11-16T09:15:00"),
        _record("t4", "paused", "Draft the weekly summary",
                updated_at="2026-11-16T09:00:00"),
        _record("t5", "completed", "Send the invoice",
                outcomes=SUCCESS, updated_at="2026-11-16T10:00:00"),
        _record("t6", "cancelled", "Archive the old drafts",
                outcomes=SUCCESS, updated_at="2026-11-16T09:00:00"),
        _record("t7", "failed", "Upload the presentation",
                outcomes=FAILED, updated_at="2026-11-16T08:00:00"),
    ]


# ------------------------------------------------------------------
# The model, headless
# ------------------------------------------------------------------

class TestActivityModel(unittest.TestCase):
    def test_four_weights_and_the_ladder(self):
        # Build 2: the headline line is 20, the heading and the
        # row value share the 16 rung (weight separates them),
        # metadata is 13. The tuple keeps the four ROLES in
        # order — headline, heading, row, metadata.
        self.assertEqual(ra.WEIGHTS, (20, 16, 16, 13))
        self.assertEqual(ra.TYPE_HEADLINE, 20)
        self.assertEqual(ra.TYPE_HEADING, 16)
        self.assertEqual(ra.TYPE_ROW, 16)
        self.assertEqual(ra.TYPE_METADATA, 13)
        # The 20, 16 and 16 are ladder rungs (13 x 1.2^n), inside
        # the Home build's 1.5 px tolerance — not new weights.
        for size in (ra.TYPE_HEADLINE, ra.TYPE_HEADING, ra.TYPE_ROW):
            best = min(abs(size - 13 * 1.2 ** n) for n in range(-2, 6))
            self.assertLessEqual(best, ra.LADDER_TOLERANCE_PX,
                                 f"{size} off-ladder")
        # The caption half of the metadata level is the tree's
        # caption token (the doc rounds it to 11).
        self.assertEqual(ra.TYPE_CAPTION, tokens.TYPE_CAPTION)

    def test_tokens_match_style_tokens(self):
        # Build 2: the 28/23/19 tokens are deleted. The headline
        # line is the title rung (20); the heading is the row
        # rung (16), semibold.
        self.assertEqual(ra.TYPE_HEADLINE, tokens.TYPE_TITLE)
        self.assertEqual(ra.TYPE_HEADING, tokens.TYPE_ROW)
        self.assertEqual(ra.TYPE_ROW, tokens.TYPE_ROW)
        self.assertEqual(ra.TYPE_METADATA, tokens.TYPE_BODY)

    def test_headline_counts(self):
        records = _dense_records()
        # The mapping is the premise: pin it before the counts.
        from gui.task_state import render_task
        self.assertEqual(render_task(records[2])["state"],
                         "Needs approval")
        self.assertEqual(render_task(records[3])["state"], "Running")
        self.assertEqual(render_task(records[5])["state"], "Partial")
        counts = ra.headline_counts(records)
        self.assertEqual(counts["live"], 4)
        self.assertEqual(counts["waiting"], 1)
        self.assertEqual(counts["finished"], 3)
        self.assertEqual(counts["latest_finished"], "Send the invoice")
        self.assertEqual(counts["attention"], 2)
        self.assertEqual(counts["total"], 7)

    def test_headline_line(self):
        counts = {"live": 4, "waiting": 1, "finished": 3,
                  "latest_finished": "Send the invoice",
                  "attention": 2, "total": 7}
        self.assertEqual(ra.headline_line("in_progress", counts),
                         "In progress — 4 · 1 waiting on you")
        self.assertEqual(ra.headline_line("history", counts),
                         "Finished — 3 · latest: Send the invoice")
        self.assertEqual(ra.headline_line("mission_log", counts),
                         "Need attention — 2 · of 7 missions")
        # No context part when there is nothing true to say.
        quiet = dict(counts, waiting=0, latest_finished="")
        self.assertEqual(ra.headline_line("in_progress", quiet),
                         "In progress — 4")
        self.assertEqual(ra.headline_line("history", quiet),
                         "Finished — 3")
        # An empty section says so in words instead of a 0.
        empty = dict(counts, live=0, waiting=0, finished=0,
                     latest_finished="", attention=0, total=0)
        self.assertEqual(ra.headline_line("in_progress", empty),
                         "Nothing in progress")
        self.assertEqual(ra.headline_line("history", empty),
                         "Nothing finished yet")
        self.assertEqual(ra.headline_line("mission_log", empty),
                         "Nothing needs attention")
        # A long latest title is cut, not wrapped.
        long_counts = dict(counts, latest_finished="x" * 50)
        line = ra.headline_line("history", long_counts)
        self.assertTrue(line.endswith("…"))
        self.assertLessEqual(len(line),
                             len("Finished — 3 · latest: ") + 40)
        with self.assertRaises(ValueError):
            ra.headline_line("nope", counts)

    def test_due_today(self):
        today = datetime.date(2026, 11, 16)
        reminders = [
            {"fire_at": "2026-11-16 09:00", "fired": False},
            {"fire_at": "2026-11-16 17:00", "fired": False},
            {"fire_at": "2026-11-17 08:00", "fired": False},
            {"fire_at": "2026-11-16 07:30", "fired": True},
            {"fire_at": "not-a-date", "fired": False},
        ]
        self.assertEqual(ra.due_today(reminders, today), (2, 3))
        self.assertEqual(ra.scheduled_line(reminders, today),
                         "2 due today · next 09:00 · 1 already spoken")
        # Due == upcoming: no "already spoken" when none fired.
        only_today = reminders[:2]
        self.assertEqual(ra.scheduled_line(only_today, today),
                         "2 due today · next 09:00")
        # Nothing due today, next one tomorrow.
        only_tomorrow = reminders[2:3]
        self.assertEqual(ra.scheduled_line(only_tomorrow, today),
                         "Nothing due today · next tomorrow 08:00")
        # Nothing at all: one honest line.
        self.assertEqual(ra.scheduled_line([], today),
                         "Nothing due today")
        # An unparseable fire_at never becomes the next slot.
        self.assertEqual(
            ra.scheduled_line([{"fire_at": "not-a-date",
                                "fired": False}], today),
            "Nothing due today")

    def test_error_line(self):
        self.assertEqual(
            ra.error_line("Task history is unavailable right now.",
                          "the ledger could not be read",
                          "Reopen Activity to try again"),
            "Task history is unavailable right now — "
            "the ledger could not be read. "
            "Reopen Activity to try again.")
        # Unknown cause: the app says so, never "something went wrong".
        line = ra.error_line("Journal is unavailable right now.",
                             None, "Reopen Activity to try again")
        self.assertIn(ra.UNKNOWN_WHY, line)
        self.assertNotIn("something went wrong", line.lower())
        self.assertEqual(
            ra.error_line("X.", "", "Try again"), "X — " +
            ra.UNKNOWN_WHY + ". Try again.")

    def test_strip_absolute_paths(self):
        # Field-check fix #8 (U5 #32/#33): a user-facing error
        # line names the file, never where the user keeps it.
        self.assertEqual(
            ra.strip_absolute_paths(
                "task ledger unreadable at /home/hatch/data/"
                "task_ledger.json: Expecting property name"),
            "task ledger unreadable at task_ledger.json: "
            "Expecting property name")
        self.assertEqual(
            ra.strip_absolute_paths(
                "tool receipts corrupt at "
                "C:\\Users\\aayur\\data\\tool_receipts.jsonl:1: bad"),
            "tool receipts corrupt at tool_receipts.jsonl:1: bad")
        self.assertEqual(
            ra.strip_absolute_paths("ledger locked"), "ledger locked")
        self.assertEqual(
            ra.strip_absolute_paths(
                "journal file task_receipts.jsonl is corrupt"),
            "journal file task_receipts.jsonl is corrupt")
        self.assertEqual(ra.strip_absolute_paths(""), "")

    def test_progress_frac(self):
        self.assertEqual(ra.progress_frac("2/5 steps done"), "2/5")
        self.assertEqual(ra.progress_frac("0/3 steps done"), "0/3")
        self.assertEqual(ra.progress_frac("no steps defined"), "")
        self.assertEqual(ra.progress_frac(""), "")

    def test_state_property(self):
        self.assertEqual(ra.state_property("Failed"), "danger")
        self.assertEqual(ra.state_property("Partial"), "amber")
        self.assertEqual(ra.state_property("Needs approval"), "amber")
        self.assertEqual(ra.state_property("Needs an answer"), "amber")
        self.assertEqual(ra.state_property("Needs a decision"), "amber")
        self.assertEqual(ra.state_property("Done"), "ok")
        # The quiet states fall back to the base rule (no override).
        self.assertEqual(ra.state_property("Running"), "text_muted")
        self.assertEqual(ra.state_property("Unknown"), "text_muted")

    def test_changes_table_names_a_way_back(self):
        self.assertTrue(ra.CHANGES)
        for change in ra.CHANGES:
            for key in ("element", "was", "now", "way_back"):
                self.assertTrue(change[key],
                                f"change without {key}: {change}")
            self.assertGreater(len(change["way_back"]), 20,
                               f"way back too short: {change}")

    def test_readability_floor_on_the_pairs_the_page_shows(self):
        for name, fg, bg, floor in ra.READABILITY_PAIRS:
            ratio = contrast_ratio(getattr(tokens, fg),
                                   getattr(tokens, bg))
            self.assertGreaterEqual(ratio, floor,
                                    f"{name}: {ratio:.2f}")

    def test_greyscale_hierarchy_survives_desaturation(self):
        order = [tokens.TEXT, tokens.TEXT_MUTED, tokens.TEXT_FAINT]
        luminances = [luminance(c) for c in order]
        self.assertEqual(luminances, sorted(luminances, reverse=True))
        self.assertLess(luminance(tokens.BG), min(luminances) / 4)


class TestActivityGuards(unittest.TestCase):
    def test_model_is_qt_free_and_literal_free(self):
        src = (ROOT / "gui" / "room_activity.py").read_text()
        self.assertNotIn("PySide6", src)
        self.assertNotIn("PyQt", src)
        self.assertEqual(
            len(re.findall(r"#[0-9A-Fa-f]{3,8}\b|rgb\(|rgba\(", src)),
            0, "colour literal in the model")

    def test_stylesheet_has_the_activity_rules(self):
        src = (ROOT / "gui" / "style_sheet.py").read_text()
        for fragment in (
                "QLabel#activityHeadline",
                # Build 2: the headline line is the title rung
                # (20 Sora semibold), the section heading the row
                # rung (16 semibold) — the 28/23 tokens are gone.
                "font-size: ${TYPE_TITLE}px",
                "QLabel#activitySection",
                "font-size: ${TYPE_ROW}px",
                "QLabel#activityRowValue",
                'QLabel#activityRowLabel[rowstate="danger"]',
                'QLabel#activityRowLabel[rowstate="amber"]',
                'QLabel#activityRowLabel[rowstate="ok"]',
                "QWidget#activityRow { border-bottom: 1px solid",
                "QListWidget QWidget#activityRow { border-bottom: none; }",
                "QListWidget#activityList::item",
                "QPushButton#activityTextAction",
                # B7F3: the circled "?" help button's own rule.
                "QPushButton#activityHelpButton",
                "QLabel#activityErrorDot",
        ):
            self.assertIn(fragment, src, f"missing QSS rule: {fragment}")
        # Build 2: the deleted tokens leave no trace in the
        # stylesheet — the headline and section rules moved to
        # the title and row rungs.
        self.assertNotIn("TYPE_DISPLAY", src)
        self.assertNotIn("TYPE_HEADING", src)
        self.assertNotIn("TYPE_LEAD", src)
        self.assertNotIn("activityHeadlineLabel", src)
        self.assertNotIn("activityHeadlineCompare", src)

    def test_setstylesheet_count_still_183(self):
        # UI rethink Build 3 (2026-11-16): 144 -> 150. The docked
        # status strip adds 4 call sites (row sheet, dot, word,
        # popover + its line labels) and the Settings search
        # empty-state rows add 2 (title + body) — all token-based
        # ({TYPE_BODY}px / {TYPE_CARD_TITLE}px), zero literals.
        # UI rethink Build 4 (2026-11-17): 150 -> 160. The new
        # Always-allow screen adds 9 call sites
        # (gui/always_allow_dialog.py: first line, never-list
        # sentence, terms title, the 6 paragraph labels via one
        # loop site, empty label, status label, and the three row
        # labels thing/terms/when) and the confirm dialog's
        # folder button adds 1 (its amber warning label in
        # gui/window_services_confirm.py). Same count as the
        # audit2s1 pin, same delta, proven with that test's own
        # gui/*.py method (150 on the backup, 160 on the live
        # tree). Each site styles a distinct widget; no widget
        # is styled twice.
        # Field-check fix #22 (2026-11-18): 160 -> 162. The
        # cloud-key wizard's _advance_from_key_page adds 2 call
        # sites in gui/onboarding_flow.py — the key page's status
        # label in DANGER red for a short key and in AMBER for no
        # key (both tokens from gui/style.py, zero literals).
        # One widget, two warning states — the label's colour is
        # the warning, so the second site is a state change, not
        # a duplicate styling. Proven with this test's own
        # gui/*.py method: 160 on backup_fieldfix_r2n4_cloudkey_pre
        # (pre-#22), 162 on backup_fieldfix_r2n3_search_pre
        # (post-#22) and on the live tree. The pin went stale
        # because no neighbour run between #22 and #26 included
        # this suite; this re-pin restores it. Same count as the
        # audit2s1 pin, same delta.
        # Rethink Build 6 (2026-11-18): 162 -> 172. Net +10, all
        # in Build 6 files: gui/onboarding_pages.py +4 (the perm
        # row name/state + the sounds row name/state),
        # gui/onboarding.py +1 (the Advanced-setup quiet link),
        # gui/pulse_page.py +1 (a metric-row value),
        # gui/senses_page.py +2 (the sense-row name/state),
        # gui/window_memory_web.py +1, gui/window_pages_system.py
        # +1 (the Doctor Reset red text). The net hides churn:
        # 16 sites added, 6 removed — the Doctor hero title
        # 16px -> 20px semibold (2 sites), the Pulse footer
        # label gone with the footer, its sub/value labels
        # restyled, a finding title restyled. Each site styles
        # a distinct widget; no widget is styled twice. Proven
        # with this test's own gui/*.py method: 162 on
        # backup_rethink_build6_pre, 172 on the live tree.
        # Same count as the audit2s1 pin, same delta.
        # Round-3 section E fix E-F302 option D (2026-11-20):
        # 172 -> 171. Net -1: the "You can switch any time"
        # note moved out of the scrolling card pages into the
        # dialog chrome — the two in-page note stylesheets
        # (gui/onboarding_pages.py, choice + profile pages)
        # are gone and one chrome-note stylesheet
        # (gui/onboarding.py) is added. The audit2s1 ceiling
        # pin (<= 172) still passes at 171.
        # Round-3 section E fix E-F201 (2026-11-21):
        # 171 -> 172. Net +1: the Theme-packs JSON box
        # (gui/window_settings_advcards.py) carries the same
        # hand-fix stylesheet as the Voice card's
        # pronunciations box — a bare QPlainTextEdit renders
        # as a white box on the dark theme without it. One
        # site, one widget; proven with this test's own
        # gui/*.py method: 171 on the backup, 172 on the
        # live tree, the single added line being the pack
        # input's setStyleSheet call. The audit2s1 ceiling
        # pin (<= 172) still passes at 172.
        # Wizard polish (2026-11-21): 172 -> 173. Net +1: the
        # Done page's brain line (gui/onboarding_pages.py)
        # moves from setObjectName("muted") to its own
        # setStyleSheet — full text colour, semibold, so the
        # recap reads as the point of the page instead of a
        # third muted line. One site, one widget; proven with
        # this test's own gui/*.py method: 172 on
        # backup_r3wizard_polish_pre, 173 on the live tree,
        # the single added line being the brain line's
        # setStyleSheet call. The audit2s1 ceiling pin moves
        # with it (<= 172 -> <= 173).
        # Round-3 OAuth build (2026-11-21): 173 -> 175. Net
        # +2: gui/onboarding_pages.py +3 (the key page's 3
        # status sites go with the page; the sign-in page's
        # handlers add 6 — the status label's OK, muted
        # not-signed-in, muted waiting-for-browser, DANGER
        # failed-validation, and muted signed-out states, plus
        # the test label's OK-or-DANGER state — all states of
        # two labels, zero literals) and
        # gui/onboarding_flow.py -1 (the key advance's 2 sites
        # go with the page; the sign-in advance's amber
        # no-sign-in warning adds 1). Proven with this test's
        # own gui/*.py method: 173 on
        # backup_r3oauth_build_pre, 175 on the live tree. The
        # audit2s1 ceiling pin moves with it (<= 173 -> <= 175).
        # Round-3 wizard expansion (2026-11-21): 175 -> 176. Net
        # +1: the Done page's greeting line
        # (gui/onboarding_pages.py) gains its own setStyleSheet
        # — the localized hello + name reads in the full text
        # colour like the brain line above it. One site, one
        # widget; proven with this test's own gui/*.py method:
        # 175 on backup_r3wizard_expand_pre, 176 on the live
        # tree, the single added line being the greeting line's
        # setStyleSheet call. The audit2s1 ceiling pin moves
        # with it (<= 175 -> <= 176).
        # Stage 0 features batch 1 (2026-11-22): 176 -> 183.
        # Net +7, all in the new views mixin
        # (gui/window_pages_views.py): the calendar widget's
        # dark sheet, the "Next 7 days" title, the day-detail
        # title, the inbox body, the contacts body, the calls
        # table, and the calls status line — 7 sites, each one
        # widget, all token-based ({TYPE_CARD_TITLE}px /
        # {TEXT_MUTED} / token colours), zero literals. A bare
        # QCalendarWidget/QTextBrowser/QTableWidget renders in
        # Qt's default light palette on the dark theme without
        # them (the eye-check caught white grids and unreadable
        # rows). Proven with this test's own gui/*.py method:
        # 176 on stage0f1_work/prescratch, 183 on the live
        # tree. The neighbour run measured 179 because its
        # workers rsynced before the 4 render-fix sheets
        # (calendar, inbox body, contacts body, calls table)
        # landed — the pre-launch mixin had only 3 sites. The
        # audit2s1 ceiling pin moves with it (<= 176 -> <= 183).
        count = 0
        for path in (ROOT / "gui").glob("*.py"):
            count += path.read_text().count("setStyleSheet")
        self.assertEqual(count, 183)

    def test_error_paths_never_touch_the_orb(self):
        for path in ("gui/window_memory_activity.py",
                     "gui/journal_surface.py"):
            src = (ROOT / path).read_text()
            self.assertNotIn("orb", src.lower(), f"orb in {path}")

    def test_pinned_strings_survive(self):
        activity = (ROOT / "gui" / "window_memory_activity.py"
                    ).read_text()
        for literal in (
                "Task history is unavailable right now.",
                "Mission log is unavailable right now.",
                "Journal is unavailable right now.",
                "TaskLedger", "all_records",
                "render_task", "add_task_row", "pending_used"):
            self.assertIn(literal, activity, f"lost pin: {literal}")
        pages = (ROOT / "gui" / "window_pages_activity.py").read_text()
        for literal in ("Resume interrupted tasks", "survive a restart",
                        "_activity_tabs", "_activity_in_progress_list",
                        "_activity_history_list", "_mission_log_list",
                        "_mission_log_detail"):
            self.assertIn(literal, pages, f"lost pin: {literal}")


# ------------------------------------------------------------------
# Qt: the real window
# ------------------------------------------------------------------

try:
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    WindowBase = object  # type: ignore[assignment]
    _QT = False


def _px(widget):
    """The widget's font size in px (stylesheet sets px sizes)."""
    font = widget.font()
    if font.pixelSize() > 0:
        return font.pixelSize()
    return font.pointSizeF()


@unittest.skipUnless(_QT, "Qt unavailable")
class ActivityWindowTests(WindowBase):
    """The REAL MainWindow offscreen — Activity as built."""

    def setUp(self):
        super().setUp()
        self._facade_scheduler = None
        self._tmpdir = None

    def tearDown(self):
        if self._facade_scheduler is not None:
            import voice_assistant.core.gui_facade as facade
            facade.scheduler = self._facade_scheduler
            self._facade_scheduler = None
        super().tearDown()

    # -- seeding ----------------------------------------------------
    def _seed_dense(self):
        """A dense morning: 7 ledger records, 3 reminders, 2
        routines, 2 plans — then open Activity."""
        import tempfile
        import voice_assistant.core.gui_facade as facade

        data_dir = ROOT / "data"
        (data_dir / "task_ledger.json").write_text(
            json.dumps({"tasks": _dense_records()}), encoding="utf-8")
        (data_dir / "plans.json").write_text(json.dumps([
            {"id": "plan-1", "goal": "Prepare the quarterly review",
             "steps": [
                 {"text": "Gather the numbers", "status": "done"},
                 {"text": "Draft the summary", "status": "done"},
                 {"text": "Send it to the team", "status": "pending"}]},
            {"id": "plan-2", "goal": "Plan the weekend trip",
             "steps": [
                 {"text": "Pick the destination", "status": "done"},
                 {"text": "Book the train", "status": "running"}]},
        ]), encoding="utf-8")
        # Reminders through the REAL scheduler (temp store), patched
        # onto the facade — the seam reminders_view reads through.
        # The harness stubs voice_assistant.core.scheduler; the
        # REAL class lives in core/scheduler.py (stdlib-only
        # imports, no side effects beyond constructing its own
        # default instance at import time). Swap the stub out,
        # import the real class, put the stub back.
        _stub_scheduler = sys.modules.pop(
            "voice_assistant.core.scheduler", None)
        try:
            from voice_assistant.core.scheduler import ProactiveScheduler
        finally:
            if _stub_scheduler is not None:
                sys.modules["voice_assistant.core.scheduler"] = (
                    _stub_scheduler)
        # The scheduler refuses past fire times, so its injectable
        # clock is pinned to 01:00 today: the morning's fire times
        # are all after it, whatever hour the suite runs at.
        self._tmpdir = pathlib.Path(
            tempfile.mkdtemp(prefix="scifi_activity_sched_"))
        now = datetime.datetime.now()
        today = now.date()
        self._seed_today = today
        real = ProactiveScheduler(
            store_path=self._tmpdir / "reminders.json",
            now_fn=lambda: datetime.datetime.combine(
                today, datetime.time(1, 0)))
        real.add_reminder(
            "r1", datetime.datetime.combine(today, datetime.time(9, 0)),
            "Standup in ten minutes")
        real.add_reminder(
            "r2", datetime.datetime.combine(today, datetime.time(17, 0)),
            "Call the electrician")
        real.add_reminder(
            "r3",
            datetime.datetime.combine(
                today + datetime.timedelta(days=1),
                datetime.time(8, 0)),
            "Water the plants")
        real.add_reminder(
            "r4", datetime.datetime.combine(today, datetime.time(7, 30)),
            "Morning alarm")
        # A fired reminder: flip the in-memory flag on the record
        # the scheduler itself holds (the tick loop is what sets
        # it in production; here the flag is the seed).
        for stored in real._reminders:
            if stored.label == "r4":
                stored.fired = True
        # Routines through the real planner.
        result = facade.planner.save_routine(
            "Morning briefing run",
            actions=[{"tool": "set_reminder",
                      "args": {"message": "Standup in ten minutes",
                               "minutes": 10}}],
            schedule="daily 07:30")
        self.assertFalse(str(result).startswith("Could not save"),
                         result)
        result = facade.planner.save_routine(
            "Evening tidy",
            actions=[{"tool": "set_reminder",
                      "args": {"message": "Tidy the desk",
                               "minutes": 5}}])
        self.assertFalse(str(result).startswith("Could not save"),
                         result)
        self._facade_scheduler = facade.scheduler
        facade.scheduler = real
        self.window._show_page(1)
        self.app.processEvents()

    def _show_activity(self):
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — the grab works either way
            pass
        self.window.resize(1280, 800)
        self.window.show()
        self.app.processEvents()

    def _activity_labels(self):
        """Activity-scoped labels, by objectName."""
        names = {"activityHeadline", "activitySection",
                 "activityRowLabel", "activityRowValue",
                 "activityRowNumber", "activityError",
                 "activityErrorDot"}
        from PySide6.QtWidgets import QLabel
        return [w for w in self.window.findChildren(QLabel)
                if w.objectName() in names]

    def _headline(self, section):
        entry = self.window._activity_headlines[section]
        return entry["line"].text()

    def _pending_buttons(self):
        return [getattr(self.window, name) for name in (
            "_activity_pending_btn", "_activity_question_btn",
            "_activity_reconcile_btn", "_activity_recovery_btn")]

    # -- the density checks ------------------------------------------
    def test_headline_lines(self):
        self._seed_dense()
        self.assertEqual(self._headline("in_progress"),
                         "In progress — 4 · 1 waiting on you")
        self.assertEqual(self._headline("scheduled"),
                         "2 due today · next 09:00 · 1 already spoken")
        self.assertEqual(self._headline("history"),
                         "Finished — 3 · latest: Send the invoice")
        self.assertEqual(self._headline("mission_log"),
                         "Need attention — 2 · of 7 missions")

    def test_headline_line_is_largest_and_no_fifth_size(self):
        self._seed_dense()
        self._show_activity()
        sizes = {}
        for label in self._activity_labels():
            sizes.setdefault(_px(label), []).append(label.objectName())
        # The three sizes: 20 headline line, 16 heading + row,
        # and the metadata level (13 small / 11.5 caption — one
        # level). Qt rounds the 11.5px caption token to a 12px
        # rendered size, so the metadata level renders as 13 + 12.
        levels = set()
        for size in sizes:
            if size == 20:
                levels.add("headline")
            elif size == 16:
                levels.add("heading+row")
            elif size in (13, 12):
                levels.add("metadata")
            else:
                self.fail(f"fifth weight on the page: {size}px "
                          f"on {sizes[size]}")
        self.assertEqual(levels, {"headline", "heading+row",
                                  "metadata"})
        # And the headline line is the largest thing on the page.
        line = self.window._activity_headlines[
            "in_progress"]["line"]
        self.assertEqual(_px(line), 20)
        for label in self._activity_labels():
            if label is not line:
                self.assertLessEqual(_px(label), 20)

    def test_task_row_is_an_instrument(self):
        self._seed_dense()
        from gui.task_state import render_task
        from PySide6.QtWidgets import QLabel, QPushButton
        rows = self.window._activity_in_progress_list
        self.assertEqual(rows.count(), 4)
        for i in range(rows.count()):
            widget = rows.itemWidget(rows.item(i))
            self.assertEqual(widget.objectName(), "activityRow")
            labels = widget.findChildren(QLabel)
            texts = [lbl.text() for lbl in labels]
            # One state label (mapped case, verbatim), one reading.
            self.assertEqual(len(labels), 3)  # state + title + number
            rendered = render_task(_dense_records()[i])
            self.assertIn(rendered["state"], texts)
            self.assertIn(rendered["title"], texts)
            number = [lbl for lbl in labels
                      if lbl.objectName() == "activityRowNumber"]
            self.assertEqual(len(number), 1)
            from gui.room_activity import progress_frac
            self.assertEqual(
                number[0].text(),
                progress_frac(rendered["progress"]))
            details = [b for b in widget.findChildren(QPushButton)
                       if b.text() == "Details"]
            self.assertEqual(len(details), 1)
            self.assertEqual(details[0].objectName(),
                             "activityTextAction")
            # The old meta line is gone: no label on the row
            # carries the muted objectName it used to have.
            for lbl in labels:
                self.assertNotEqual(lbl.objectName(), "muted")
        # The squeeze regression (render-found): QListWidget::item
        # padding lays the widget out inside the padded area, so
        # a row whose item ignores the padding renders with
        # 5px-tall labels. The labels must stand at full height.
        self._show_activity()
        for i in range(rows.count()):
            widget = rows.itemWidget(rows.item(i))
            self.assertGreaterEqual(widget.height(), 18,
                                    "row widget squeezed")
            for lbl in widget.findChildren(QLabel):
                self.assertGreaterEqual(lbl.height(), 15,
                                        "row label clipped")

    def test_pending_row_is_quiet_text_primary_first(self):
        """Build 2: every visible queue is a quiet text action —
        no primary button anymore. The first queue in the fixed
        order comes first."""
        self._seed_dense()
        buttons = self._pending_buttons()
        visible = [b for b in buttons if not b.isHidden()]
        self.assertGreaterEqual(len(visible), 1)
        for button in visible:
            self.assertEqual(button.objectName(),
                             "activityTextAction",
                             f"{button.text()} is not quiet text")
            self.assertNotEqual(button.objectName(), "primaryBtn")
        # The approvals queue comes first here; recovery second.
        self.assertEqual([b.text() for b in visible],
                         ["Review pending approvals (1)",
                          "Resume interrupted tasks (3)"])

    def test_filters_are_one_quiet_row_and_frozen(self):
        self._seed_dense()
        from PySide6.QtWidgets import QPushButton, QScrollArea
        # Scope to the Mission Log tab: other pages carry their
        # own "Done" buttons, and findChildren on the window
        # would count them too.
        tab = self.window._activity_tabs.widget(3)
        chips = [b for b in tab.findChildren(QPushButton)
                 if b.text() in ("All", "Running", "Done", "Failed",
                                 "Partial")]
        # The five state chips: quiet, checkable, one row.
        self.assertEqual(len(chips), 5)
        for chip in chips:
            self.assertEqual(chip.objectName(), "chipBtn")
            self.assertTrue(chip.isCheckable())
        # Frozen: no scroll area between a chip and the tab.
        ancestor = chips[0].parentWidget()
        while ancestor is not None:
            self.assertNotIsInstance(ancestor, QScrollArea,
                                     "filter row scrolls away")
            ancestor = ancestor.parentWidget()
        # The list below it does scroll.
        lst = self.window._mission_log_list
        self.assertIsNotNone(lst.verticalScrollBar())

    def test_reminder_rows(self):
        self._seed_dense()
        from PySide6.QtWidgets import QLabel
        lst = self.window.reminders_list
        self.assertEqual(lst.count(), 4)
        seen = {}
        for i in range(lst.count()):
            widget = lst.itemWidget(lst.item(i))
            self.assertEqual(widget.objectName(), "activityRow")
            labels = {lbl.objectName(): lbl.text()
                      for lbl in widget.findChildren(QLabel)}
            seen[labels["activityRowValue"]] = labels
            # No item text: QListWidget paints an item's text
            # behind its widget, so text here would double-paint
            # over the row.
            self.assertEqual(lst.item(i).text(), "")
            if labels["activityRowValue"] == "Morning alarm":
                # The fired row dims through the fired property.
                self.assertEqual(widget.property("fired"), "true")
        self.assertEqual(seen["Standup in ten minutes"]
                         ["activityRowNumber"],
                         f"{self._seed_today.isoformat()} 09:00")
        self.assertEqual(seen["Standup in ten minutes"]
                         ["activityRowLabel"], "REMINDER")

    def test_plan_and_routine_rows(self):
        self._seed_dense()
        from PySide6.QtWidgets import QLabel, QPushButton
        plans = self.window.plans_layout
        plan_rows = [plans.itemAt(i).widget()
                     for i in range(plans.count())]
        self.assertEqual(len(plan_rows), 2)
        by_goal = {}
        for row in plan_rows:
            labels = {lbl.objectName(): lbl.text()
                      for lbl in row.findChildren(QLabel)}
            by_goal[labels["activityRowValue"]] = (row, labels)
        row, labels = by_goal["Prepare the quarterly review"]
        self.assertEqual(labels["activityRowLabel"], "PLAN")
        self.assertEqual(labels["activityRowNumber"], "2/3")
        buttons = [b.text() for b in row.findChildren(QPushButton)]
        self.assertIn("Resume", buttons)
        self.assertIn("Details", buttons)
        row, labels = by_goal["Plan the weekend trip"]
        self.assertEqual(labels["activityRowLabel"],
                         "PLAN · INTERRUPTED")
        self.assertEqual(labels["activityRowNumber"], "1/2")
        routines = self.window.routines_layout
        routine_rows = [routines.itemAt(i).widget()
                        for i in range(routines.count())]
        self.assertEqual(len(routine_rows), 2)
        for row in routine_rows:
            labels = {lbl.objectName(): lbl.text()
                      for lbl in row.findChildren(QLabel)}
            self.assertEqual(labels["activityRowLabel"], "ROUTINE")
            # The editor hides behind the Schedule toggle: the
            # row's second child widget starts hidden.
            from PySide6.QtWidgets import QWidget as _QWidget
            children = [c for c in row.children()
                        if isinstance(c, _QWidget)]
            self.assertEqual(len(children), 2)
            editor = children[1]
            self.assertTrue(editor.isHidden(),
                            "routine editor is not hidden by default")
            schedule = [b for b in row.findChildren(QPushButton)
                        if b.text() == "Schedule"]
            self.assertEqual(len(schedule), 1)
            schedule[0].click()
            self.app.processEvents()
            self.assertFalse(editor.isHidden(),
                             "Schedule toggle did not open the editor")

    def test_corrupt_journal_row_is_an_error_line(self):
        self._seed_dense()
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QPushButton
        data_dir = ROOT / "data"
        with open(data_dir / "tool_receipts.jsonl", "a",
                  encoding="utf-8") as fh:
            fh.write("{broken\n")
        self.window._refresh_activity_tabs()
        self.app.processEvents()
        lst = self.window._activity_journal_list
        self.assertEqual(lst.count(), 1)
        # No item text: the widget's line is the row's only text.
        self.assertEqual(lst.item(0).text(), "")
        self.assertTrue(lst.item(0).data(
            Qt.ItemDataRole.UserRole)["corrupt"])
        widget = lst.itemWidget(lst.item(0))
        self.assertIsNotNone(widget, "corrupt row is not a widget row")
        dots = [lbl for lbl in widget.findChildren(QLabel)
                if lbl.objectName() == "activityErrorDot"]
        self.assertEqual(len(dots), 1)
        self.assertEqual(dots[0].text(), "●")
        lines = [lbl for lbl in widget.findChildren(QLabel)
                 if lbl.objectName() == "activityError"]
        self.assertEqual(len(lines), 1)
        self.assertIn("unreadable", lines[0].text().lower())
        details = [b for b in widget.findChildren(QPushButton)
                   if b.text() == "Details"]
        self.assertEqual(len(details), 1)
        # Field-check fix #8 (U5 #33): the row names the file,
        # not the path — no absolute path, no raw store error
        # on the row face. The raw error stays on the item's
        # UserRole data: the Details dialog IS the manual-repair
        # surface (its repair steps name the file to open), so
        # the path belongs there, not on the reading row.
        self.assertEqual(
            lines[0].text(),
            "Your journal file is unreadable. "
            "Details shows how to repair it.")
        self.assertNotIn("/home/", lines[0].text())
        self.assertNotIn("tool receipts corrupt at", lines[0].text())
        self.assertIn(
            "tool receipts corrupt at",
            lst.item(0).data(Qt.ItemDataRole.UserRole)["error"])

    def test_unavailable_rows_are_error_lines(self):
        self._seed_dense()
        import voice_assistant.core.gui_facade as facade
        from PySide6.QtWidgets import QLabel
        # The mock stays up through processEvents(): window
        # construction posts a 1.5 s singleShot
        # (_run_startup_recovery_check, window_shell_frame.py)
        # that re-refreshes Activity when the loop pumps. If the
        # mock has already exited, that refresh reads the real
        # ledger and the error row is replaced by real rows — a
        # harness race, not a product bug (the product re-reads
        # the ledger fresh on every refresh, so it shows the
        # error row whenever the ledger is really unavailable).
        with mock.patch.object(
                facade, "TaskLedger",
                mock.Mock(side_effect=RuntimeError("ledger locked"))):
            self.window._refresh_activity_tabs()
            self.app.processEvents()
        # Field-check fix #8 (U5 #32): the unavailable rows are
        # §2f error-line WIDGETS (red dot + one line), not
        # plain-text items — the item carries no text, the
        # widget's line is the row's only text.
        for lst, what in (
                (self.window._activity_in_progress_list,
                 "Task history is unavailable right now"),
                (self.window._activity_history_list,
                 "Task history is unavailable right now"),
                (self.window._mission_log_list,
                 "Mission log is unavailable right now")):
            self.assertEqual(lst.count(), 1)
            self.assertEqual(lst.item(0).text(), "")
            widget = lst.itemWidget(lst.item(0))
            self.assertIsNotNone(widget,
                                 "unavailable row is not a widget row")
            dots = [lbl for lbl in widget.findChildren(QLabel)
                    if lbl.objectName() == "activityErrorDot"]
            self.assertEqual(len(dots), 1)
            self.assertEqual(dots[0].text(), "●")
            lines = [lbl for lbl in widget.findChildren(QLabel)
                     if lbl.objectName() == "activityError"]
            self.assertEqual(len(lines), 1)
            first = lines[0].text()
            # No period after "now": the sentence runs on into the
            # cause ("— ledger locked."), which is the correct shape.
            self.assertIn(what, first)
            self.assertIn("Reopen Activity to try again.", first)
        in_progress = self.window._activity_in_progress_list
        widget = in_progress.itemWidget(in_progress.item(0))
        lines = [lbl for lbl in widget.findChildren(QLabel)
                 if lbl.objectName() == "activityError"]
        self.assertIn("ledger locked", lines[0].text())

    def test_unavailable_rows_carry_no_absolute_path(self):
        # Field-check fix #8 (U5 #32): a really corrupt ledger
        # file used to print its absolute path on the row. The
        # cause keeps the file NAME (identifiable) without saying
        # where the user keeps their data.
        self._seed_dense()
        data_dir = ROOT / "data"
        (data_dir / "task_ledger.json").write_text(
            "{broken", encoding="utf-8")
        self.window._refresh_activity_tabs()
        self.app.processEvents()
        from PySide6.QtWidgets import QLabel
        for lst in (self.window._activity_in_progress_list,
                    self.window._activity_history_list,
                    self.window._mission_log_list):
            self.assertEqual(lst.count(), 1)
            widget = lst.itemWidget(lst.item(0))
            self.assertIsNotNone(widget)
            lines = [lbl for lbl in widget.findChildren(QLabel)
                     if lbl.objectName() == "activityError"]
            self.assertEqual(len(lines), 1)
            text = lines[0].text()
            self.assertIn("task_ledger.json", text)
            self.assertNotIn("/home/", text)
            self.assertNotIn(str(data_dir), text)

    def test_morning_use_stays_dense_and_calm(self):
        """Density check 5, code half: add a reminder, open a
        routine's schedule, and the figures and row counts stand
        — nothing re-layouts under the work."""
        self._seed_dense()
        before_in_progress = self.window._activity_in_progress_list.count()
        before_history = self.window._activity_history_list.count()
        before_line = self._headline("in_progress")
        # Add a reminder through the real scheduler, as the
        # morning would.
        import voice_assistant.core.gui_facade as facade
        now = datetime.datetime.now()
        facade.scheduler.add_reminder(
            "r5",
            datetime.datetime.combine(now.date(),
                                      datetime.time(18, 0)),
            "Pick up the dry cleaning")
        self.window._refresh_reminders()
        self.app.processEvents()
        self.assertEqual(self.window.reminders_list.count(), 5)
        # The new reminder is due today, so the Scheduled line
        # moves "2 due today" -> "3 due today" — the line
        # following the work is the point; the rest of the page
        # is what must not move.
        self.assertEqual(
            self._headline("scheduled"),
            "3 due today · next 09:00 · 1 already spoken")
        # Open a routine's schedule editor.
        routines = self.window.routines_layout
        row = routines.itemAt(0).widget()
        from PySide6.QtWidgets import QPushButton
        schedule = [b for b in row.findChildren(QPushButton)
                    if b.text() == "Schedule"][0]
        schedule.click()
        self.app.processEvents()
        # The rest of the page did not move.
        self.assertEqual(self.window._activity_in_progress_list.count(),
                         before_in_progress)
        self.assertEqual(self.window._activity_history_list.count(),
                         before_history)
        self.assertEqual(self._headline("in_progress"), before_line)
        visible = [b for b in self._pending_buttons()
                   if not b.isHidden()]
        for button in visible:
            self.assertEqual(button.objectName(),
                             "activityTextAction")

    def test_journal_actions_appear_only_on_selection(self):
        """Build 2: Reconcile / Compensate do not exist on the
        page while nothing is selected — no dimmed buttons. The
        hint line names them while an actionable row exists."""
        self._seed_dense()
        from PySide6.QtCore import Qt
        import voice_assistant.core.gui_facade as facade
        store = facade.tool_receipts.ReceiptStore()
        attempt = store.record_attempt(
            tool="menu_action", args={"x": "menu_action"},
            handle_id="h-unknown", snapshot_stamp="s-unknown")
        store.mark_dispatched(str(attempt["receipt_id"]))
        self.window._refresh_activity_tabs()
        self.app.processEvents()
        reconcile = self.window._activity_journal_reconcile_btn
        compensate = self.window._activity_journal_compensate_btn
        hint = self.window._activity_journal_hint
        lst = self.window._activity_journal_list
        self.assertGreaterEqual(lst.count(), 1)
        # One row, one row tall: the list does not stretch to
        # fill the pane (Build 2 — the pane hugs, the list
        # does not soak up its slack). This row wraps to two
        # lines, so the bar is its own row height.
        self.assertLessEqual(lst.height(),
                             lst.sizeHintForRow(0) + 6)
        # Nothing selected: no buttons, the hint names them.
        self.assertTrue(reconcile.isHidden())
        self.assertTrue(compensate.isHidden())
        self.assertFalse(hint.isHidden())
        self.assertEqual(hint.text(),
                         "Select entries to reconcile or compensate")
        # Select the unknown row: Reconcile appears, hint goes.
        # No pump after the selection: the selection signal updates
        # the buttons synchronously, and pumping lets the window's
        # 1.5 s startup singleShot (_run_startup_recovery_check)
        # fire a refresh that clears the list and drops the
        # selection — the same race as
        # test_unavailable_rows_are_error_lines. The pump above
        # (after the refresh) already flushed what was pending.
        lst.setCurrentRow(0)
        self.assertFalse(reconcile.isHidden())
        self.assertTrue(hint.isHidden())
        # Clear the selection: back to hint, no buttons.
        lst.clearSelection()
        self.assertTrue(reconcile.isHidden())
        self.assertFalse(hint.isHidden())

    def test_remove_selected_appears_only_on_selection(self):
        """Build 2: Remove selected is not in the add row and
        does not exist while nothing is selected."""
        self._seed_dense()
        btn = self.window._reminders_remove_btn
        lst = self.window.reminders_list
        self.assertTrue(btn.isHidden())
        self.assertEqual(btn.objectName(), "activityTextAction")
        # Pump once BEFORE selecting, to flush the window's 1.5 s
        # startup singleShot (_run_startup_recovery_check): it
        # refreshes Activity, which clears this list and drops
        # any selection. The selection signal updates the button
        # synchronously, so no pump is needed after selecting —
        # and pumping there is what let the timer steal the
        # selection (same race as
        # test_unavailable_rows_are_error_lines).
        self.app.processEvents()
        lst.setCurrentRow(0)
        self.assertFalse(btn.isHidden())
        lst.clearSelection()
        self.assertTrue(btn.isHidden())

    def test_reminders_section_head(self):
        """Build 2: the reminders section has a head — its name
        at 16 semibold, Refresh as quiet text there. The old
        summary label is gone: its line IS the Scheduled
        headline now."""
        self._seed_dense()
        from PySide6.QtWidgets import QLabel, QPushButton
        tab = self.window._activity_tabs.widget(1)
        heads = [w for w in tab.findChildren(QLabel)
                 if w.objectName() == "activitySection"
                 and w.text() == "Reminders"]
        self.assertEqual(len(heads), 1)
        self.assertEqual(_px(heads[0]), 16)
        refresh = [b for b in tab.findChildren(QPushButton)
                   if b.text() == "Refresh"]
        self.assertEqual(len(refresh), 1)
        self.assertEqual(refresh[0].objectName(),
                         "activityTextAction")
        self.assertFalse(hasattr(self.window, "reminders_summary"))

    def test_fire_at_times_pad_to_two_digits(self):
        """Build 2: the clock fields read as a time ("14:00"),
        and the checkbox row sits under the add row."""
        self._seed_dense()
        self._show_activity()
        self.assertEqual(self.window.input_reminder_m.text(), "00")
        self.assertEqual(len(self.window.input_reminder_h.text()), 2)
        self.assertGreater(self.window.chk_reminder_at.y(),
                           self.window.input_reminder_msg.y())

    def test_scheduled_help_button(self):
        """Build 2: the "?" beside the Scheduled headline opens
        the explainer that used to sit on the page."""
        self._seed_dense()
        from unittest import mock
        from PySide6.QtWidgets import QPushButton
        tab = self.window._activity_tabs.widget(1)
        helps = [b for b in tab.findChildren(QPushButton)
                 if b.text() == "?"]
        self.assertEqual(len(helps), 1)
        # B7F3: the "?" is a circled button now — its own
        # objectName (activityHelpButton), not the shared
        # borderless text-action chrome.
        self.assertEqual(helps[0].objectName(), "activityHelpButton")
        import gui.design_system.dialogs as dialogs
        with mock.patch.object(
                dialogs.HudDialog, "information") as info:
            helps[0].click()
            self.app.processEvents()
        self.assertEqual(info.call_count, 1)
        self.assertIn("survive a restart", info.call_args[0][2])

    def test_pane_hugs_the_current_tab(self):
        """Build 2: the bordered pane ends after the current
        tab's content — below it is plain background, not a
        void inside the border. History (three rows) draws a
        much shorter pane than In progress."""
        self._seed_dense()
        self._show_activity()
        tabs = self.window._activity_tabs
        tabs.setCurrentIndex(0)
        self.app.processEvents()
        in_progress_h = tabs.height()
        tabs.setCurrentIndex(2)
        self.app.processEvents()
        history_h = tabs.height()
        self.assertLess(history_h, in_progress_h,
                        "History pane did not hug its three rows")
        self.assertLess(history_h, self.window.height() * 0.6,
                        "History pane still runs to the window bottom")
        self.assertLess(in_progress_h, self.window.height(),
                        "In progress pane fills the window")

    def test_render_activity_dense(self):
        """The grabs the five density verdicts are judged from."""
        from PySide6.QtGui import QImage
        self._seed_dense()
        self._show_activity()
        RENDER_DIR.mkdir(parents=True, exist_ok=True)
        tabs = self.window._activity_tabs
        names = ["activity_in_progress.png", "activity_scheduled.png",
                 "activity_history.png", "activity_mission_log.png"]
        for index, name in enumerate(names):
            tabs.setCurrentIndex(index)
            self.app.processEvents()
            grab = self.window.grab()
            grab.save(str(RENDER_DIR / name))
            if index == 0:
                grey = grab.toImage().convertToFormat(
                    QImage.Format.Format_Grayscale8)
                grey.save(str(RENDER_DIR
                              / "activity_in_progress_grey.png"))
        # The routines + plans live below the fold of the
        # Scheduled tab's scroll area — bring the first plan row
        # into view (a bare setValue(maximum) lands before the
        # layout has settled its maximum).
        tabs.setCurrentIndex(1)
        self.app.processEvents()
        plans_layout = self.window.plans_layout
        first_plan = plans_layout.itemAt(0).widget()
        self.window.reminders_scroll.ensureWidgetVisible(first_plan)
        self.app.processEvents()
        grab = self.window.grab()
        grab.save(str(RENDER_DIR / "activity_scheduled_routines.png"))
        # The corrupt-journal error line, as the user would see it.
        data_dir = ROOT / "data"
        with open(data_dir / "tool_receipts.jsonl", "a",
                  encoding="utf-8") as fh:
            fh.write("{broken\n")
        self.window._refresh_activity_tabs()
        tabs.setCurrentIndex(0)
        self.app.processEvents()
        grab = self.window.grab()
        grab.save(str(RENDER_DIR / "activity_journal_corrupt.png"))
        for name in names + ["activity_in_progress_grey.png",
                             "activity_scheduled_routines.png",
                             "activity_journal_corrupt.png"]:
            path = RENDER_DIR / name
            self.assertTrue(path.exists(), f"missing grab {name}")
            self.assertGreater(path.stat().st_size, 0, name)


if __name__ == "__main__":
    unittest.main()
