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
_forget_learned_fact/_forget_all_learned): the header adds the
card's row count to the list's shown/total, in both the plain and
the "X of Y facts shown" forms; the card's Forget paths re-read
the list + header too.

Counting rule, pinned here: header == list rows + card rows, in
every state (empty, the section-E 14/22 shape on both the real
learning path and the section-E staging seam, the 100-fact
stress, forget-while-open, filtered search/category, singular).
The learned facts ARE rendered twice (card + list) — that double
rendering is the page's structure, not this fix; the fix makes
the header agree with it.

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
    """memory_header_text: the counting rule, pinned without Qt."""

    def test_plain_counts_list_plus_card(self):
        # Section E, real path: 22 list rows + 8 card rows.
        self.assertEqual(memory_header_text(22, 22, 8),
                         "30 facts remembered")

    def test_staging_shape(self):
        # Section E as filed: 14 list rows + 8 card rows.
        self.assertEqual(memory_header_text(14, 14, 8),
                         "22 facts remembered")

    def test_stress(self):
        self.assertEqual(memory_header_text(114, 114, 100),
                         "214 facts remembered")

    def test_singular(self):
        self.assertEqual(memory_header_text(1, 1, 0),
                         "1 fact remembered")

    def test_zero(self):
        self.assertEqual(memory_header_text(0, 0, 0),
                         "0 facts remembered")

    def test_of_form_counts_card_in_both(self):
        # Filtered: 1 list row shown + 1 card row, of 15 + 1.
        self.assertEqual(memory_header_text(1, 15, 1),
                         "2 of 16 facts shown")

    def test_no_learned_is_the_old_line(self):
        self.assertEqual(memory_header_text(14, 14, 0),
                         "14 facts remembered")
        self.assertEqual(memory_header_text(1, 15, 0),
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
    """The real Memory page: the header equals list + card rows."""

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

    def _assert_header_equals_rows(self):
        shown, total = self._header_numbers()
        self.assertEqual(shown, self._list_rows() + self._card_rows(),
                         f"header {self._header()!r} vs rows "
                         f"list={self._list_rows()} "
                         f"card={self._card_rows()}")

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
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self._show_memory()
        self.assertEqual(self._header(), "1 fact remembered")
        self._assert_header_equals_rows()

    def test_section_e_shape_real_path(self):
        # 14 hand-saved + 8 learned through the real learner, one
        # shared store: 22 list rows + 8 card rows = 30 on the page.
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self.assertEqual(self._header(), "30 facts remembered")
        self.assertEqual(self._list_rows(), 22)
        self.assertEqual(self._card_rows(), 8)
        self._assert_header_equals_rows()

    def test_section_e_shape_staging_seam(self):
        # The filed shape exactly: 14 in the store, the 8 learned
        # facts served outside it. Pre-fix the header said 14.
        self._seed_hand_saved(14)
        self.window._learning_module = lambda: _FakeLearning(
            LEARNED_FIXTURES)
        self._show_memory()
        self.assertEqual(self._header(), "22 facts remembered")
        self.assertEqual(self._list_rows(), 14)
        self.assertEqual(self._card_rows(), 8)
        self._assert_header_equals_rows()

    def test_section_e_shape_at_every_width(self):
        self._seed_hand_saved(14)
        self.window._learning_module = lambda: _FakeLearning(
            LEARNED_FIXTURES)
        self._show_memory()
        for width in (908, 1024, 1280, 1920):
            self.window.resize(width, 750)
            self.app.processEvents()
            self.assertEqual(self._header(), "22 facts remembered",
                             f"at width {width}")
            self._assert_header_equals_rows()

    def test_stress_staging_seam(self):
        self._seed_hand_saved(14)
        stress = [{"id": 1000 + i, "display": f"Learned fact {i + 1}",
                   "key": f"fact {i + 1}", "value": f"value {i + 1}"}
                  for i in range(100)]
        self.window._learning_module = lambda: _FakeLearning(stress)
        self._show_memory()
        self.assertEqual(self._header(), "114 facts remembered")
        self.assertEqual(self._list_rows(), 14)
        self.assertEqual(self._card_rows(), 100)
        self._assert_header_equals_rows()

    def test_stress_real_path(self):
        self._seed_hand_saved(14)
        for i in range(100):
            self.assertTrue(self._store.save_fact(
                "learned", f"fact {i + 1}", f"value {i + 1}"))
        self._show_memory()
        self.assertEqual(self._header(), "214 facts remembered")
        self.assertEqual(self._list_rows(), 114)
        self.assertEqual(self._card_rows(), 100)
        self._assert_header_equals_rows()

    def test_forget_learned_updates_header_without_reshow(self):
        # The second E-F204 hole: forgetting from the card left the
        # header (and the list) stale until the next page show.
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self.assertEqual(self._teach(LEARN_SENTENCES[:2]), 2)
        self._show_memory()
        self.assertEqual(self._header(), "5 facts remembered")
        facts = self._learn.get_learned_facts()
        self.assertEqual(len(facts), 2)
        self.window._forget_learned_fact(facts[0]["id"],
                                         facts[0]["display"])
        self.app.processEvents()
        self.assertEqual(self._header(), "3 facts remembered")
        self.assertEqual(self._list_rows(), 2)
        self.assertEqual(self._card_rows(), 1)
        self._assert_header_equals_rows()

    def test_forget_all_learned_updates_header(self):
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self.assertEqual(self._teach(LEARN_SENTENCES[:2]), 2)
        self._show_memory()
        self.assertEqual(self._header(), "5 facts remembered")
        # Two-tap bulk forget: first tap arms, second erases.
        self.window._forget_all_learned()
        self.window._forget_all_learned()
        self.app.processEvents()
        self.assertEqual(self._header(), "1 fact remembered")
        self.assertEqual(self._list_rows(), 1)
        self.assertEqual(self._card_rows(), 0)
        self._assert_header_equals_rows()

    def test_search_names_shown_rows(self):
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES[:1]), 1)
        self._show_memory()
        self.window.mem_search.setText("dentist")
        self.app.processEvents()
        # The list narrows to the Dentist row; the card still shows
        # the (unmatching) learned row — 2 rows shown of 16.
        self.assertEqual(self._header(), "2 of 16 facts shown")
        self._assert_header_equals_rows()

    def test_category_learned_names_shown_rows(self):
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES[:1]), 1)
        self._show_memory()
        combo = self.window.mem_cat_filter
        idx = combo.findText("learned")
        self.assertGreaterEqual(idx, 0, "no 'learned' category")
        combo.setCurrentIndex(idx)
        self.app.processEvents()
        self.assertEqual(self._header(), "2 of 16 facts shown")
        self._assert_header_equals_rows()

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
        # the web's fact nodes.
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self.assertEqual(self._header(), "30 facts remembered")
        self._show_web()
        self.assertEqual(self._header(), "22 facts remembered")
        self.assertEqual(self._web_nodes(), 22)

    def test_web_view_names_fact_nodes_staging_seam(self):
        self._seed_hand_saved(14)
        self.window._learning_module = lambda: _FakeLearning(
            LEARNED_FIXTURES)
        self._show_memory()
        self.assertEqual(self._header(), "22 facts remembered")
        self._show_web()
        # The web reads the store, so the 8 learned facts outside
        # it are not nodes: 14 nodes, header 14.
        self.assertEqual(self._header(), "14 facts remembered")
        self.assertEqual(self._web_nodes(), 14)

    def test_web_view_header_follows_switches(self):
        self._seed_hand_saved(14)
        self.assertEqual(self._teach(LEARN_SENTENCES), 8)
        self._show_memory()
        self.assertEqual(self._header(), "30 facts remembered")
        self._show_web()
        self.assertEqual(self._header(), "22 facts remembered")
        self.window._set_memory_view("list")
        self._pump(0.3)
        self.assertEqual(self._header(), "30 facts remembered")
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
        # on the page, not the 114 store total.
        self._seed_hand_saved(14)
        for i in range(100):
            self.assertTrue(self._store.save_fact(
                "learned", f"fact {i + 1}", f"value {i + 1}"))
        self._show_memory()
        self.assertEqual(self._header(), "214 facts remembered")
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
        self.assertEqual(self._header(), "114 facts remembered")
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
