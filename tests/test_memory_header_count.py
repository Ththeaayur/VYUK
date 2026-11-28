"""Field-check fix #28 (round-2 UI finding 2): the Memory page header
read "0 facts remembered" while the "Learned about you" card below
listed the fact.

Root cause: the header count label (mem_count_label) is set only by
_refresh_memories, and _show_page(2) — the single path onto the
Memory page — refreshed the learned card and the web but never called
_refresh_memories. The header therefore kept its build-time value
("0 facts remembered") until something else (a search keystroke, a
category change, an add/forget) happened to refresh it. The fix is
one call: _show_page(2) now also calls _refresh_memories, so the
header counts the same store the page lists, every time the page
opens. The header already counted the right set (every fact in the
store — the hand-saved list shows them all, unfiltered, learned ones
included); only the refresh was missing.

E-F204 (round-3 section E finding): the header STILL understated
the page — "14 facts remembered" while 22 facts were on it (14
hand-saved in the list + 8 learned in the card above it), and
"14" while 114 were shown in the 100-fact stress. The header
counted only the store total, but the page renders the learned
facts twice — once in the card, once in the list. The E-F204 fix
made the header count the facts ON the page: list rows + card
rows. That rule is SUPERSEDED by the hunt fix (c) header round
(2026-11-26, parent ruling): the item-6 widening made the card
render every user fact — the same facts already in the list — so
the old rule counted every fact twice ("10 facts remembered" for
5 facts, "2" for 1). The header now counts UNIQUE facts (the
store's shown/total); the card's second rendering never adds.
The expectations below pin that rule (1 learned fact reads
"1 fact remembered": 1 unique fact in the store, rendered twice
on the page); the old list-plus-card numbers were the E-F204 bug,
not the rule.

Two halves:
  * HeaderViewTests (pure, both interpreters): memory_facts_view
    counts every fact in the store, whatever its category.
  * HeaderWindowTests (Qt only, WindowBase harness): the real Memory
    page — header, Learned card and hand-saved list agree on every
    show, including the round-2 sequence (page opened, fact learned
    later, page re-opened).

Harness note (same seam family as the cap tests): WindowBase stubs
voice_assistant.core.memory.memory, so the facade's memory_store is
a _Fake whose get_all_facts() returns [] — the header would be pinned
at 0 on every tree. The window tests therefore bridge the facade's
memory_store to the harness temp store (the production invariant:
header and card read the ONE shared store singleton) and drive the
Learned card through the flat core.memory.learning module, the way
the cap tests do. The bridge is removed in tearDown so the next
test's window resolves the facade lazily again.
"""

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui.memory_view import memory_facts_view  # noqa: E402

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = object  # type: ignore[assignment]


class _Store:
    """Minimal memory_store stand-in: rows are plain fact dicts."""

    def __init__(self, rows):
        self._rows = list(rows)

    def get_all_facts(self):
        return list(self._rows)

    def search_facts(self, query):
        q = query.lower()
        return [r for r in self._rows
                if q in str(r.get("key", "")).lower()
                or q in str(r.get("value", "")).lower()
                or q in str(r.get("category", "")).lower()]


def _fact(category, key, value):
    return {"id": len(key), "category": category, "key": key,
            "value": value}


class HeaderViewTests(unittest.TestCase):
    """memory_facts_view: the total counts every fact, any category."""

    def test_total_counts_learned_and_general_together(self):
        store = _Store([
            _fact("learned", "note_my_dentist_is_dr", "my dentist is Dr. Rao"),
            _fact("general", "favourite_drink", "Masala tea"),
        ])
        view = memory_facts_view(store=store)
        self.assertEqual(view["total"], 2)
        self.assertEqual(view["shown"], 2)

    def test_total_is_zero_on_an_empty_store(self):
        view = memory_facts_view(store=_Store([]))
        self.assertEqual(view["total"], 0)
        self.assertEqual(view["shown"], 0)
        self.assertEqual(view["facts"], [])

    def test_category_filter_narrows_shown_but_not_total(self):
        store = _Store([
            _fact("learned", "note_my_dentist_is_dr", "my dentist is Dr. Rao"),
            _fact("general", "favourite_drink", "Masala tea"),
        ])
        view = memory_facts_view(category="learned", store=store)
        self.assertEqual(view["total"], 2)
        self.assertEqual(view["shown"], 1)
        self.assertEqual(view["facts"][0]["category"], "learned")


@unittest.skipUnless(_QT, "Qt unavailable")
class HeaderWindowTests(WindowBase):
    """The real Memory page: header, card and list agree on show."""

    def setUp(self):
        super().setUp()
        self._orig_feed = os.environ.get("VYUK_LEARNING_FEED_DIR")
        os.environ["VYUK_LEARNING_FEED_DIR"] = str(self._tmp / "feed")
        # Bridge the facade's store to the harness temp store (see
        # module docstring): in production header and card share one
        # store; the harness stub would otherwise pin the header at 0.
        import voice_assistant.core.gui_facade as facade
        self._facade = facade
        facade.memory_store = self._store
        # Drive the Learned card through the flat learning module
        # (the cap tests' seam — the facade cannot resolve learning
        # under the harness shim).
        from core.memory import learning as module
        self._learn = module
        self._orig_learn_store = module.memory_store
        module.memory_store = self._store
        self.window._learning_module = lambda: module

    def tearDown(self):
        try:
            self._learn.memory_store = self._orig_learn_store
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        try:
            # Remove the bridge so the facade resolves lazily again
            # for the next test's window (its own stubbed store).
            delattr(self._facade, "memory_store")
        except Exception:  # noqa: BLE001 — best-effort restore
            pass
        if self._orig_feed is None:
            os.environ.pop("VYUK_LEARNING_FEED_DIR", None)
        else:
            os.environ["VYUK_LEARNING_FEED_DIR"] = self._orig_feed
        super().tearDown()

    # -- helpers ------------------------------------------------------
    def _teach(self, text):
        stored = self._learn.learn_from_text(text)
        self.assertEqual(len(stored), 1, f"did not store: {text!r}")
        return stored[0]

    def _show_memory(self):
        # Force List view: the "memory/view" QSettings key persists
        # across tests in one process (the QSettings path is cached
        # per process), so a test run after any web-switching test
        # would otherwise build the page in Web view and read the
        # web header here. Same shape as the E-F204 suite's helper.
        self.window._show_page(2)
        self.window._set_memory_view("list")
        self.app.processEvents()

    def _header(self):
        return self.window.mem_count_label.text()

    def _card_rows(self):
        """Learned-fact rows on the card: layout items carrying a
        Forget button (the empty state is art + a label, no button)."""
        from PySide6.QtWidgets import QPushButton
        rows = 0
        texts = []
        layout = self.window.learned_layout
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is None:
                continue
            from PySide6.QtWidgets import QLabel
            labels = [lb.text() for lb in widget.findChildren(QLabel)]
            if any(b.text() == "Forget"
                   for b in widget.findChildren(QPushButton)):
                rows += 1
                texts.extend(labels)
        return rows, texts

    def _fact_rows(self):
        """Fact rows in the list: items carrying a fact id.
        Rethink Build 6 groups the list by recency with a header
        item per group, so the raw count() includes headers — the
        fact count is the items with an id."""
        from PySide6.QtCore import Qt

        return sum(
            1 for i in range(self.window.memory_list.count())
            if self.window.memory_list.item(i).data(
                Qt.ItemDataRole.UserRole) is not None)

    # -- tests --------------------------------------------------------
    def test_one_learned_fact_header_counts_it(self):
        self._teach("remember that my dentist is Dr. Rao")
        self._show_memory()
        # Hunt fix (c) header round: the header counts unique
        # facts — one stored fact reads 1, even though the card
        # renders it a second time (list + card). The old E-F204
        # rule read 2 here.
        self.assertEqual(self._header(), "1 fact remembered")
        rows, texts = self._card_rows()
        self.assertEqual(rows, 1)
        self.assertTrue(any("Dr. Rao" in t for t in texts))
        self.assertEqual(self._fact_rows(), 1)

    def test_several_facts_header_counts_all(self):
        self._teach("remember that my dentist is Dr. Rao")
        self._teach("remember that my favourite colour is azure")
        self._teach("remember that my dog is called Biscuit")
        self._store.save_fact("general", "Favourite drink", "Masala tea")
        self._show_memory()
        # Hunt fix (c) header round: 4 unique facts in the store
        # (3 learned + 1 general). The widened card shows all 4
        # (its rows stay 4 — the card side of item 6 is unchanged),
        # but its rows do not add to the header. The old E-F204
        # rule read 8 here (4 list + 4 card).
        self.assertEqual(self._header(), "4 facts remembered")
        rows, _texts = self._card_rows()
        self.assertEqual(rows, 4)
        self.assertEqual(self._fact_rows(), 4)

    def test_zero_facts_header_is_honest_zero(self):
        self._show_memory()
        self.assertEqual(self._header(), "0 facts remembered")
        rows, _texts = self._card_rows()
        self.assertEqual(rows, 0)
        self.assertEqual(self._fact_rows(), 0)

    def test_header_refreshes_on_show_after_learning(self):
        # The round-2 sequence, exactly: the page opens once on an
        # empty store, a fact is learned afterwards, the page opens
        # again — the header must follow the card, not its first
        # build-time value.
        self._show_memory()
        self.assertEqual(self._header(), "0 facts remembered")
        self.window._show_page(0)
        self.app.processEvents()
        self._teach("remember that my dentist is Dr. Rao")
        self._show_memory()
        # Hunt fix (c) header round: 1 unique fact reads 1.
        self.assertEqual(self._header(), "1 fact remembered")
        rows, texts = self._card_rows()
        self.assertEqual(rows, 1)
        self.assertTrue(any("Dr. Rao" in t for t in texts))

    def test_forget_updates_header_on_next_show(self):
        self._teach("remember that my dentist is Dr. Rao")
        self._teach("remember that my favourite colour is azure")
        self._show_memory()
        # Hunt fix (c) header round: 2 unique facts read 2.
        self.assertEqual(self._header(), "2 facts remembered")
        facts = self._learn.get_learned_facts()
        self.assertTrue(
            self._learn.forget_learned_fact(facts[0]["id"]))
        self._show_memory()
        # Hunt fix (c) header round: 1 unique fact left, reads 1.
        self.assertEqual(self._header(), "1 fact remembered")
        rows, _texts = self._card_rows()
        self.assertEqual(rows, 1)


if __name__ == "__main__":
    unittest.main()
