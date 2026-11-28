"""E-F204 (round-3 section E finding): the Memory header count
understated the page — "14 facts remembered" while 22 facts were
on it (14 hand-saved in the list + 8 learned in the "Learned about
you" card), and "14" while 114 were shown in the 100-fact stress.

Root cause: mem_count_label is set only by _refresh_memories from
memory_facts_view()["total"] — the store total — while the page
renders the learned facts a second time in the card above the list
(the card reads core.memory.learning.get_learned_facts(), a second
read of the facts). The header must count the facts actually ON
the page: list rows + card rows. A second, smaller hole rode the
same root cause: forgetting a learned fact from the card refreshed
the card but never the header (or the list), so both stayed stale
until the next page show.

The fix (gui/window_memory_web.py _refresh_memories +
gui/window_memory_learned.py _learned_fact_count +
_forget_learned_fact/_forget_all_learned): the header counts the
unique facts in the store, in both the plain and the "X of Y
facts shown" forms; the card's Forget paths re-read the list +
header too. (The E-F204 fix as filed added the card's row count
to the list's shown/total — header == list rows + card rows.
That rule is SUPERSEDED by the hunt fix (c) header round
(2026-11-26, parent ruling): the item-6 widening made the card
render every user fact — the same facts already in the list — so
the old rule counted every fact twice ("10 facts remembered"
for 5 facts, "2" for 1). The header now counts each fact ONCE.)

Counting rule, pinned here: header == unique facts (shown of
total), in every state (empty, the section-E 14/22 shape on both
the real learning path and the section-E staging seam, the
100-fact stress, forget-while-open, filtered search/category,
singular). The learned facts ARE rendered twice (card + list) —
that double rendering is the page's structure, not this fix;
the header counts each fact once, so it agrees with the store
("All (N)") and the stat strip ("N Facts"), not with the
rendering count.

E-F204 FOLLOW-UP (view-aware header): the List fix left the header
stale in Web view — it kept the List rows count (30) over a web
showing 22 fact nodes. The header is view-aware now: in Web view
it names the web's fact nodes, plain form, never the List "X of Y"
form. The web caps each category at 40 nodes (MAX_PER_CATEGORY),
so the real-path 100-learned stress shows 54 fact nodes (40
learned + 14 hand-saved), not 114. The follow-up also fixes a
pre-existing crash on the same refresh path: _sync_mem_web_tools
had `del show[c]` — a NameError whenever a category vanished
while the web widget existed, aborting the refresh mid-way.

Qt only (the real MainWindow offscreen through
tests.test_chat_ui.WindowBase); the suite skips on sys, as with
the E-F202/E-F203 suites.
"""

import os
import pathlib
import re
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui.memory_view import memory_header_text  # noqa: E402
from gui.memory_view import memory_web_header_text  # noqa: E402

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = object  # type: ignore[assignment]

# The exact section-E hand-saved seeds (slice-2 driver _seed_facts).
HAND_SAVED = [
    ("general", "Favourite drink", "Masala tea, no sugar"),
    ("preferences", "Morning briefing", "Hindi first, top three"),
    ("general", "Gym days", "Monday, Wednesday, Friday"),
    ("preferences", "Coffee order", "Flat white, oat milk"),
    ("general", "Project deadline", "MS101 report due 28 November"),
    ("preferences", "Music while working", "Lo-fi, no vocals"),
    ("general", "Sister's birthday", "Ananya, 14 March"),
    ("preferences", "Wake word", "Responds to VYUK only"),
    ("general", "Flight home", "Mumbai to Delhi, 24 December"),
    ("preferences", "News digest", "Technology and space only"),
    ("general", "Dentist", "Dr. Mehta, every six months"),
    ("preferences", "Room temperature", "24C working, 22C sleeping"),
    ("general", "Very long fact to test wrapping", "Over-long value"),
    ("general", "IIT Bombay", "First-year B.Tech IEOR, Hostel 4"),
]

LEARN_SENTENCES = [
    "remember that my name is Aayur",
    "remember that I prefer tea in the morning",
    "remember that I like my briefing in Hindi first",
    "remember that my MS101 group is O1 and the gripper uses a two-finger linkage",
    "remember that I go to the gym on Monday, Wednesday and Friday",
    "remember that I asked VYUK to keep the room at 24 degrees while working",
    "remember that my favourite drink is masala tea with no sugar",
    "remember that I have a sister named Ananya whose birthday is 14 March",
]

LEARNED_FIXTURES = [
    {"id": 101, "display": "Your name is Aayur",
     "key": "name", "value": "Aayur"},
    {"id": 102, "display": "You prefer tea in the morning",
     "key": "morning drink", "value": "tea"},
    {"id": 103, "display": "You like your briefing in Hindi first",
     "key": "briefing language", "value": "Hindi first"},
    {"id": 104,
     "display": "Your MS101 group is O1 and the gripper uses a two-finger linkage",
     "key": "MS101 group", "value": "O1, two-finger linkage gripper"},
    {"id": 105, "display": "You go to the gym on Monday, Wednesday and Friday",
     "key": "gym days", "value": "Mon/Wed/Fri"},
    {"id": 106, "display": "You asked VYUK to keep the room at 24°C while working",
     "key": "room temperature", "value": "24°C working"},
    {"id": 107, "display": "Your favourite drink is masala tea with no sugar",
     "key": "favourite drink", "value": "masala tea, no sugar"},
    {"id": 108, "display": "You have a sister named Ananya whose birthday is 14 March",
     "key": "sister", "value": "Ananya, birthday 14 March"},
]


class _FakeLearning:
    """The slice-2 driver's seam: the learned facts live OUTSIDE
    the store, exactly as section E staged them."""

    def __init__(self, facts):
        self._facts = list(facts)

    def get_learned_facts(self):
        return list(self._facts)

    def forget_learned_fact(self, fid):
        before = len(self._facts)
        self._facts = [f for f in self._facts if f.get("id") != fid]
        return len(self._facts) < before

    def forget_all_learned(self):
        n = len(self._facts)
        self._facts = []
        return n


class HeaderTextTests(unittest.TestCase):
    """memory_header_text: the counting rule, pinned without Qt.

    The header counts UNIQUE facts: shown of total, where shown
    and total are the list's (store's) counts. The card renders
    the same facts a second time, but its rows never add — there
    is no third argument any more (the old `learned` parameter
    is gone with the E-F204 adding rule)."""

    def test_plain_counts_unique_facts(self):
        # Section E, real path: 22 unique facts in the store.
        # The card renders the same 22 a second time; they do
        # not add.
        self.assertEqual(memory_header_text(22, 22),
                         "22 facts remembered")

    def test_staging_shape_counts_store_only(self):
        # Section E as filed: 14 stored facts. (The 8 staged
        # card facts live outside the store — a test-only seam —
        # so the pure helper, which counts stored facts, reads
        # 14; the Qt staging-seam test covers the page.)
        self.assertEqual(memory_header_text(14, 14),
                         "14 facts remembered")

    def test_stress(self):
        self.assertEqual(memory_header_text(114, 114),
                         "114 facts remembered")

    def test_singular(self):
        self.assertEqual(memory_header_text(1, 1),
                         "1 fact remembered")

    def test_zero(self):
        self.assertEqual(memory_header_text(0, 0),
                         "0 facts remembered")

    def test_of_form_counts_unique_facts(self):
        # Filtered: 1 list row shown of 15 stored facts.
        self.assertEqual(memory_header_text(1, 15),
                         "1 of 15 facts shown")

    def test_plain_and_of_forms(self):
        self.assertEqual(memory_header_text(14, 14),
                         "14 facts remembered")
        self.assertEqual(memory_header_text(1, 15),
                         "1 of 15 facts shown")


class WebHeaderTextTests(unittest.TestCase):
    """memory_web_header_text: the Web-view rule, pinned without Qt.

    In Web view the card + list are hidden and the web shows one
    node per kept store fact, so the header names the web's fact
    nodes — always the plain form, never the List "X of Y" form."""

    def test_plain_counts_fact_nodes(self):
        self.assertEqual(memory_web_header_text(22),
                         "22 facts remembered")

    def test_singular(self):
        self.assertEqual(memory_web_header_text(1),
                         "1 fact remembered")

    def test_zero(self):
        self.assertEqual(memory_web_header_text(0),
                         "0 facts remembered")

    def test_stress_counts_kept_nodes_not_store_total(self):
        # The web caps each category at 40 nodes: 100 learned keep
        # 40, the 14 hand-saved keep all 14 — 54 nodes on the page.
        self.assertEqual(memory_web_header_text(54),
                         "54 facts remembered")


@unittest.skipUnless(_QT, "Qt unavailable")
class HeaderCountTests(WindowBase):
    """The real Memory page: the header counts unique facts
    (shown of total) — never the card's rows on top."""

    def setUp(self):
        super().setUp()
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = str(self._tmp / "feed")
        # Bridge BOTH readers to the ONE harness temp store (the
        # production invariant: header and card share one store
        # singleton) — test_memory_header_count's seam.
        import voice_assistant.core.gui_facade as facade
        self._facade = facade
        facade.memory_store = self._store
        from core.memory import learning as module
        self._learn = module
        self._orig_learn_store = module.memory_store
        module.memory_store = self._store
        self.window._learning_module = lambda: module
        # The Web view reads window_memory_web's OWN module-global
        # memory_store (bound at import from the facade), NOT the
        # facade attribute — the E-F202 harness trap. Without this
        # the web refreshes from the real (empty) store and shows
        # only the "you" node.
        import voice_assistant.gui.window_memory_web as _wmw
        self._wmw = _wmw
        self._orig_wmw_store = _wmw.memory_store
        _wmw.memory_store = self._store

    def tearDown(self):
        try:
            self._wmw.memory_store = self._orig_wmw_store
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        try:
            self._learn.memory_store = self._orig_learn_store
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        try:
            delattr(self._facade, "memory_store")
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        super().tearDown()

    # -- helpers ------------------------------------------------------
    def _pump(self, secs=0.5):
        end = time.monotonic() + secs
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.02)

    def _show_memory(self):
        # Force List view: the "memory/view" QSettings key persists
        # across tests in one process (the QSettings path is cached
        # per process), so a test that ran after a web test would
        # otherwise build the page in Web view and read the web
        # header here. Same shape as the repro's show_memory().
        self.window._show_page(2)
        self.window._set_memory_view("list")
        self.app.processEvents()

    def _show_web(self):
        self.window._set_memory_view("web")
        self._pump(0.5)

    def _web_nodes(self):
        """Fact nodes the web graph shows right now."""
        graph = self.window.memory_web.canvas2d.graph
        return sum(1 for n in graph.nodes
                   if getattr(n, "kind", "") == "fact")

    def _header(self):
        return self.window.mem_count_label.text()

    def _header_numbers(self):
        """(shown, total) from the header text."""
        text = self._header()
        m = re.match(r"(\d+) of (\d+) facts shown", text)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.match(r"(\d+) facts? remembered", text)
        if m:
            return int(m.group(1)), int(m.group(1))
        self.fail(f"unparsable header: {text!r}")

    def _card_rows(self):
        from PySide6.QtWidgets import QPushButton
        rows = 0
        layout = self.window.learned_layout
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            if any(b.text() == "Forget"
                   for b in w.findChildren(QPushButton)):
                rows += 1
        return rows

    def _list_rows(self):
        from PySide6.QtCore import Qt
        return sum(
            1 for i in range(self.window.memory_list.count())
            if self.window.memory_list.item(i).data(
                Qt.ItemDataRole.UserRole) is not None)

    def _assert_header_counts_unique_facts(self):
        # Hunt fix (c) header round: the header names unique
        # facts — shown == the list rows, total == the store
        # total. The card renders the same facts a second time,
        # so its rows never add (the old E-F204 rule asserted
        # shown == list + card here).
        shown, total = self._header_numbers()
        self.assertEqual(shown, self._list_rows(),
                         f"header {self._header()!r} shown vs list "
                         f"{self._list_rows()}")
        self.assertEqual(total, len(self._store.get_all_facts()),
                         f"header {self._header()!r} total vs store "
                         f"{len(self._store.get_all_facts())}")

    def _seed_hand_saved(self, n=14):
        for cat, key, val in HAND_SAVED[:n]:
            self.assertTrue(self._store.save_fact(cat, key, val), key)

    def _teach(self, sentences):
        got = 0
        for s in sentences:
            got += len(self._learn.learn_from_text(s) or [])
        return got

    # -- tests --------------------------------------------------------
    def test_empty_header_is_zero(self):
        self._show_memory()
        self.assertEqual(self._header(), "0 facts remembered")
        self.assertEqual(self._list_rows(), 0)
        self.assertEqual(self._card_rows(), 0)

    def test_singular_hand_saved_only(self):
        # Hunt fix (c) header round: the header counts unique
        # facts — one stored fact reads 1, even though the
        # widened card renders it a second time (list + card).
        # The old E-F204 rule read 2 here.
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self._show_memory()
        self.assertEqual(self._header(), "1 fact remembered")
        self._assert_header_counts_unique_facts()

    def test_section_e_shape_real_path(self):
        # 14 hand-saved + 8 learned through the real learner, one
        # shared store. Hunt fix (c) header round: the header
        # counts unique facts — 22 in the store — even though the
        # widened card renders all 22 a second time (22 list rows
        # + 22 card rows). The old E-F204 rule read 44 here.
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self.assertEqual(self._header(), "22 facts remembered")
        self.assertEqual(self._list_rows(), 22)
        self.assertEqual(self._card_rows(), 22)
        self._assert_header_counts_unique_facts()

    def test_section_e_shape_staging_seam(self):
        # The filed shape exactly: 14 in the store, the 8 learned
        # facts served outside it. Hunt fix (c) header round: the
        # header counts unique STORED facts, so it reads 14 — the
        # 8 staged facts live outside the store (a test-only seam;
        # in production the card's facts always come from the
        # store, so this shape cannot occur there). The old E-F204
        # rule read 22 here (14 list + 8 card rows).
        self._seed_hand_saved(14)
        self.window._learning_module = lambda: _FakeLearning(
            LEARNED_FIXTURES)
        self._show_memory()
        self.assertEqual(self._header(), "14 facts remembered")
        self.assertEqual(self._list_rows(), 14)
        self.assertEqual(self._card_rows(), 8)
        self._assert_header_counts_unique_facts()

    def test_section_e_shape_at_every_width(self):
        self._seed_hand_saved(14)
        self.window._learning_module = lambda: _FakeLearning(
            LEARNED_FIXTURES)
        self._show_memory()
        for width in (908, 1024, 1280, 1920):
            self.window.resize(width, 750)
            self.app.processEvents()
            self.assertEqual(self._header(), "14 facts remembered",
                             f"at width {width}")
            self._assert_header_counts_unique_facts()

    def test_stress_staging_seam(self):
        self._seed_hand_saved(14)
        stress = [{"id": 1000 + i, "display": f"Learned fact {i + 1}",
                   "key": f"fact {i + 1}", "value": f"value {i + 1}"}
                  for i in range(100)]
        self.window._learning_module = lambda: _FakeLearning(stress)
        self._show_memory()
        # The 100 staged facts live outside the store, so the
        # header counts the 14 stored facts only (see the
        # section-E staging-seam note above).
        self.assertEqual(self._header(), "14 facts remembered")
        self.assertEqual(self._list_rows(), 14)
        self.assertEqual(self._card_rows(), 100)
        self._assert_header_counts_unique_facts()

    def test_stress_real_path(self):
        # Hunt fix (c) header round: 114 unique facts in the
        # store (14 hand-saved + 100 learned); the widened card
        # renders all 114 a second time, but they do not add. The
        # old E-F204 rule read 228 here.
        self._seed_hand_saved(14)
        for i in range(100):
            self.assertTrue(self._store.save_fact(
                "learned", f"fact {i + 1}", f"value {i + 1}"))
        self._show_memory()
        self.assertEqual(self._header(), "114 facts remembered")
        self.assertEqual(self._list_rows(), 114)
        self.assertEqual(self._card_rows(), 114)
        self._assert_header_counts_unique_facts()

    def test_forget_learned_updates_header_without_reshow(self):
        # The second E-F204 hole: forgetting from the card left the
        # header (and the list) stale until the next page show.
        # Hunt fix (c) header round: 1 general + 2 learned = 3
        # unique facts; after forgetting one learned fact, 2.
        # (The widened card shows 3 rows, then 2 — its rows do
        # not add to the header.)
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self.assertEqual(self._teach(LEARN_SENTENCES[:2]), 2)
        self._show_memory()
        self.assertEqual(self._header(), "3 facts remembered")
        facts = self._learn.get_learned_facts()
        self.assertEqual(len(facts), 2)
        self.window._forget_learned_fact(facts[0]["id"],
                                         facts[0]["display"])
        self.app.processEvents()
        self.assertEqual(self._header(), "2 facts remembered")
        self.assertEqual(self._list_rows(), 2)
        self.assertEqual(self._card_rows(), 2)
        self._assert_header_counts_unique_facts()

    def test_forget_all_learned_updates_header(self):
        # Hunt fix (c) header round: 1 general + 2 learned = 3
        # unique facts; after the bulk forget (learned-only) the
        # 1 general fact remains, so the header reads 1.
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self.assertEqual(self._teach(LEARN_SENTENCES[:2]), 2)
        self._show_memory()
        self.assertEqual(self._header(), "3 facts remembered")
        # Two-tap bulk forget: first tap arms, second erases.
        self.window._forget_all_learned()
        self.window._forget_all_learned()
        self.app.processEvents()
        self.assertEqual(self._header(), "1 fact remembered")
        self.assertEqual(self._list_rows(), 1)
        self.assertEqual(self._card_rows(), 1)
        self._assert_header_counts_unique_facts()

    def test_search_names_shown_rows(self):
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES[:1]), 1)
        self._show_memory()
        self.window.mem_search.setText("dentist")
        self.app.processEvents()
        # The list narrows to the Dentist row; the card still shows
        # all 15 user facts (unfiltered) — but the header counts
        # unique facts, so 1 shown of 15. The old E-F204 rule
        # read "16 of 30" here.
        self.assertEqual(self._header(), "1 of 15 facts shown")
        self._assert_header_counts_unique_facts()

    def test_category_learned_names_shown_rows(self):
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES[:1]), 1)
        self._show_memory()
        combo = self.window.mem_cat_filter
        idx = combo.findText("learned")
        self.assertGreaterEqual(idx, 0, "no 'learned' category")
        combo.setCurrentIndex(idx)
        self.app.processEvents()
        # The list narrows to the 1 learned row; the card still
        # shows all 15 user facts — but the header counts unique
        # facts, so 1 shown of 15.
        self.assertEqual(self._header(), "1 of 15 facts shown")
        self._assert_header_counts_unique_facts()

    def test_no_learning_module_counts_store_only(self):
        # _learned_fact_count is guarded: no module means zero
        # card rows, and the header falls back to the store total.
        self._seed_hand_saved(14)
        self.window._learning_module = lambda: None
        self._show_memory()
        self.assertEqual(self._header(), "14 facts remembered")
        self.assertEqual(self._list_rows(), 14)
        self.assertEqual(self._card_rows(), 0)

    # -- Web view (E-F204 follow-up) ----------------------------------
    def test_web_view_names_fact_nodes_real_path(self):
        # The filed hole: List said 30 (correct), Web kept 30 over
        # a web showing 22 fact nodes. In Web view the header names
        # the web's fact nodes. Hunt fix (c) header round: the
        # List header is 22 unique facts; the Web header is still
        # 22 fact nodes.
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self.assertEqual(self._header(), "22 facts remembered")
        self._show_web()
        self.assertEqual(self._header(), "22 facts remembered")
        self.assertEqual(self._web_nodes(), 22)

    def test_web_view_names_fact_nodes_staging_seam(self):
        self._seed_hand_saved(14)
        self.window._learning_module = lambda: _FakeLearning(
            LEARNED_FIXTURES)
        self._show_memory()
        # The List header counts stored facts only (14 — see the
        # section-E staging-seam note above).
        self.assertEqual(self._header(), "14 facts remembered")
        self._show_web()
        # The web reads the store, so the 8 learned facts outside
        # it are not nodes: 14 nodes, header 14.
        self.assertEqual(self._header(), "14 facts remembered")
        self.assertEqual(self._web_nodes(), 14)

    def test_web_view_header_follows_switches(self):
        # Hunt fix (c) header round: the List header is 22 unique
        # facts; the Web header is still 22 fact nodes.
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self.assertEqual(self._header(), "22 facts remembered")
        self._show_web()
        self.assertEqual(self._header(), "22 facts remembered")
        self.window._set_memory_view("list")
        self._pump(0.3)
        self.assertEqual(self._header(), "22 facts remembered")
        self._show_web()
        self.assertEqual(self._header(), "22 facts remembered")

    def test_web_view_search_keeps_plain_form(self):
        # In Web view the search only dims non-matches — it never
        # re-filters the header into the List "X of Y" form. (A
        # search keystroke DOES re-run _refresh_memories, so the
        # header must stay plain through that path too.)
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self._show_web()
        self.window.mem_search.setText("dentist")
        self._pump(0.5)
        self.assertEqual(self._header(), "22 facts remembered")
        self.assertEqual(self._web_nodes(), 22)

    def test_web_view_stress_real_path_is_capped(self):
        # 100 learned + 14 hand-saved in the store: the web caps
        # each category at 40 nodes, so it shows 54 fact nodes
        # (40 learned + 14 hand-saved) — the header names the nodes
        # on the page, not the 114 store total. Hunt fix (c)
        # header round: the List header is 114 unique facts; the
        # Web header is still 54 nodes.
        self._seed_hand_saved(14)
        for i in range(100):
            self.assertTrue(self._store.save_fact(
                "learned", f"fact {i + 1}", f"value {i + 1}"))
        self._show_memory()
        self.assertEqual(self._header(), "114 facts remembered")
        self._show_web()
        self.assertEqual(self._header(), "54 facts remembered")
        self.assertEqual(self._web_nodes(), 54)

    def test_web_view_stress_staging_seam(self):
        self._seed_hand_saved(14)
        stress = [{"id": 1000 + i, "display": f"Learned fact {i + 1}",
                   "key": f"fact {i + 1}", "value": f"value {i + 1}"}
                  for i in range(100)]
        self.window._learning_module = lambda: _FakeLearning(stress)
        self._show_memory()
        # The List header counts stored facts only (14 — see the
        # section-E staging-seam note above).
        self.assertEqual(self._header(), "14 facts remembered")
        self._show_web()
        self.assertEqual(self._header(), "14 facts remembered")
        self.assertEqual(self._web_nodes(), 14)

    def test_web_view_empty(self):
        self._show_memory()
        self._show_web()
        self.assertEqual(self._header(), "0 facts remembered")
        self.assertEqual(self._web_nodes(), 0)

    def test_web_category_shrink_refreshes_without_crash(self):
        # _sync_mem_web_tools had `del show[c]` — a NameError
        # whenever a category vanished while the web widget
        # existed, aborting the refresh mid-way (the follow-up
        # repro hit it the moment the store's categories shrank
        # after the web was opened). Open the web, remove the
        # whole "learned" category, re-show: the refresh must
        # complete, the header must follow the web, and the dead
        # category must leave the web's Show state.
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self._show_web()
        self.assertEqual(self._header(), "22 facts remembered")
        self.assertEqual(self._web_nodes(), 22)
        for f in list(self._store.get_all_facts()):
            if str(f.get("category") or "") == "learned":
                self.assertTrue(self._store.delete_fact(f.get("id")))
        self.window._show_page(2)
        self._pump(0.5)
        self.assertEqual(self._header(), "14 facts remembered")
        self.assertEqual(self._web_nodes(), 14)
        self.assertNotIn("learned", self.window._mem_web_show or {})


if __name__ == "__main__":
    unittest.main()
