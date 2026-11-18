"""Wave 5 PULSE / memory tests.
Stdlib unittest, no GUI, no network, no transactions.

INTENTIONAL (world-view removal, 2026-11-05): the briefing /
act-on-it / trip tests are gone with the feature; the PULSE and
memory tests stay."""

import unittest

from core import nongraph_memory as memory
from core import pulse_w5 as pulse


class PulseTests(unittest.TestCase):
    def test_wikidata_card_needs_source(self):
        item = pulse.PulseItem(kind="wikidata", title="Paris")
        self.assertFalse(pulse.wikidata_card(item)["ok"])
        item.source = "wikidata"
        result = pulse.wikidata_card(item)
        self.assertTrue(result["ok"])
        self.assertEqual(result["card"]["licence"], "CC0")

    def test_wikipedia_needs_link_out(self):
        item = pulse.PulseItem(kind="wikipedia", title="Paris")
        self.assertFalse(pulse.wikipedia_summary(item)["ok"])
        item.link = "https://en.wikipedia.org/wiki/Paris"
        result = pulse.wikipedia_summary(item)
        self.assertTrue(result["ok"])
        self.assertIn("CC BY-SA", result["summary"]["attribution"])

    def test_wikipedia_store_always_refused(self):
        item = pulse.PulseItem(kind="wikipedia", title="Paris",
                               link="https://en.wikipedia.org/wiki/Paris")
        self.assertFalse(pulse.store_summary(item)["ok"])

    def test_newsdata_terms_owed_blocks(self):
        item = pulse.PulseItem(kind="newsdata", title="Headline",
                               delay_hours=24)
        self.assertFalse(pulse.newsdata_item(item)["ok"])
        self.assertTrue(pulse.newsdata_item(item, terms_read=True)["ok"])

    def test_newsdata_fresh_refused_as_breaking(self):
        item = pulse.PulseItem(kind="newsdata", title="Headline",
                               delay_hours=2)
        self.assertFalse(pulse.newsdata_item(item, terms_read=True)["ok"])

    def test_currents_spoken_summary_terms_gate(self):
        item = pulse.PulseItem(kind="currents", title="Headline")
        result = pulse.currents_spoken_summary(item)
        self.assertFalse(result["ok"])
        self.assertTrue(
            pulse.currents_spoken_summary(item, terms_read=True)["ok"])


class MemoryTests(unittest.TestCase):
    def test_note_and_search(self):
        store = memory.NonGraphMemory()
        self.assertTrue(store.add_note(
            memory.MemoryNote(title="Quake", body="M5 near coast"))["ok"])
        self.assertEqual(len(store.search("quake")), 1)

    def test_graph_edge_refused_c1(self):
        store = memory.NonGraphMemory()
        result = store.add_graph_edge("a", "b", "relates")
        self.assertFalse(result["ok"])
        self.assertIn("C-1", result["reason"])

    def test_source_finder_refuses_sourceless(self):
        out = memory.source_finder([
            {"title": "Good", "source": "usgs", "link": "x"},
            {"title": "No source", "source": ""},
        ])
        self.assertEqual(len(out["results"]), 1)
        self.assertEqual(len(out["refused"]), 1)




if __name__ == "__main__":
    unittest.main()
