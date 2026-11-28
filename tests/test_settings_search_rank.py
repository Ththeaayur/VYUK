"""Settings search ranking + Language top task (field-check fix
#23, round-2 N3).

Two halves:
  * RankTests (pure, both interpreters): core/settings_search
    match_tier / rank_cards — a title match outranks a
    body/toggle match, so the card the user means is top.
    The match itself is exactly match_cards plus the card's
    own text, so ranking never changes the visible SET.
  * RankWindowTests (Qt only, WindowBase harness): the real
    Settings page — the ranked top card for the seven real
    queries, the visible set unchanged, clear restores the
    page exactly, and every Top task lands its target in view
    (Language -> Local brain, the Advanced card that used to
    miss because show_settings_card scrolled against the
    pre-expansion layout).
"""

import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import settings_search as search  # noqa: E402

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = object  # type: ignore[assignment]

# Real card titles, in page order (build order), for the pure
# ranking tests — the same titles the Settings page shows.
_TITLES = list(search.CARD_SYNONYMS)


class RankTests(unittest.TestCase):
    """match_tier / rank_cards: relevance order, same match set."""

    def test_battery_top_is_power_and_network(self):
        # "battery" is an exact synonym of Power & network;
        # Notices only has a "Battery" toggle in its body.
        hay = {"Notices": "notices battery guardian toggle",
               "Power & network": "power network"}
        ranked = search.rank_cards("battery", _TITLES, hay)
        self.assertEqual(ranked[0], "Power & network")
        self.assertIn("Notices", ranked)

    def test_keyboard_top_is_keyboard_shortcuts(self):
        # "keyboard" is an exact synonym of Keyboard shortcuts;
        # How VYUK thinks only matches because its synonym "key"
        # is a substring of "keyboard" (phrase tier).
        ranked = search.rank_cards("keyboard", _TITLES, {})
        self.assertEqual(ranked[0], "Keyboard shortcuts")
        self.assertIn("How VYUK thinks", ranked)
        self.assertLess(ranked.index("Keyboard shortcuts"),
                        ranked.index("How VYUK thinks"))

    def test_connect_top_is_how_vyuk_thinks(self):
        # "connect" is an exact synonym of How VYUK thinks —
        # the Connect top task's target too — while Sounds only
        # has "Connect" inside its "Play a sound for…" list.
        hay = {"Sounds": "sounds play a sound for connect",
               "How VYUK thinks": "how vyuk thinks brain",
               "Browser control": "browser control connect my browser"}
        ranked = search.rank_cards("connect", _TITLES, hay)
        self.assertEqual(ranked[0], "How VYUK thinks")
        self.assertIn("Sounds", ranked)
        self.assertIn("Browser control", ranked)

    def test_theme_top_is_appearance_not_theme_packs(self):
        # "theme" is an exact synonym of Appearance; Theme packs
        # merely has "theme" in its title, so it ranks below.
        ranked = search.rank_cards("theme", _TITLES, {})
        self.assertEqual(ranked[0], "Appearance")
        self.assertLess(ranked.index("Appearance"),
                        ranked.index("Theme packs"))

    def test_mic_voice_tops_unchanged(self):
        self.assertEqual(
            search.rank_cards("mic", _TITLES, {})[0], "Microphone")
        self.assertEqual(
            search.rank_cards("voice", _TITLES, {})[0], "Voice")

    def test_backup_keeps_page_order_within_body_tier(self):
        # Both match only through their body text, so the page
        # order stands: Local brain first, Your settings file
        # second (the round-2 field check's correct order).
        hay = {"Local brain": "local brain backup language kokoro",
               "Your settings file": "settings file backup export"}
        ranked = search.rank_cards("backup", _TITLES, hay)
        self.assertEqual(ranked[:2],
                         ["Local brain", "Your settings file"])

    def test_zebra_matches_nothing(self):
        self.assertEqual(search.rank_cards("zebra", _TITLES, {}), [])
        self.assertEqual(search.rank_cards("zebra", _TITLES), [])

    def test_ranking_never_changes_the_match_set(self):
        # For every real query, rank_cards returns exactly the
        # match_cards set, in a different order. Ranking moves
        # cards; it never hides or shows one.
        for query in ("theme", "battery", "backup", "keyboard",
                      "mic", "voice", "connect", "zebra",
                      "make her quieter", "stop listening"):
            hay = {t: t.lower() for t in _TITLES}
            self.assertEqual(
                set(search.rank_cards(query, _TITLES, hay)),
                set(search.match_cards(query, _TITLES)),
                f"query {query!r}")

    def test_tier_order(self):
        self.assertEqual(
            search.match_tier("voice", "Voice", ""), 0)
        self.assertEqual(
            search.match_tier("theme", "Appearance", ""), 1)
        self.assertEqual(
            search.match_tier("theme", "Theme packs", ""), 2)
        self.assertEqual(
            search.match_tier("keyboard", "How VYUK thinks", ""), 3)
        self.assertEqual(
            search.match_tier("battery", "Notices",
                              "notices battery toggle"), 4)
        self.assertIsNone(
            search.match_tier("battery", "Notices", "notices only"))
        self.assertIsNone(search.match_tier("", "Voice", "voice"))


@unittest.skipUnless(_QT, "Qt unavailable")
class RankWindowTests(WindowBase):
    """The real Settings page, ranked in the viewport."""

    def _show_settings(self):
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — boot may already be skipped
            pass
        self.window._show_page(4)
        self.app.processEvents()

    def _viewport_titles(self):
        """Visible card titles in visual order (top first)."""
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtWidgets import QLabel
        scroll = getattr(self.window, "settings_scroll", None)
        vp = scroll.viewport().rect() if scroll is not None else None
        found = []
        for card in getattr(self.window, "_settings_card_texts", {}):
            if not card.isVisibleTo(self.window):
                continue
            y = 0
            if vp is not None and scroll is not None:
                try:
                    pos = card.mapTo(scroll.viewport(), QPoint(0, 0))
                    if not QRect(pos, card.size()).intersects(vp):
                        continue
                    y = pos.y()
                except Exception:  # noqa: BLE001
                    pass
            title = ""
            for lbl in card.findChildren(QLabel, "sectionTitle"):
                title = lbl.text()
                break
            found.append((y, title))
        found.sort(key=lambda item: item[0])
        return [t for _y, t in found]

    def _visible_set(self):
        from PySide6.QtWidgets import QLabel
        out = set()
        for card in getattr(self.window, "_settings_card_texts", {}):
            if not card.isVisibleTo(self.window):
                continue
            for lbl in card.findChildren(QLabel, "sectionTitle"):
                out.add(lbl.text())
                break
        return out

    def _search(self, query):
        self.window.settings_search.setText(query)
        self.window._on_settings_search(query)
        self.app.processEvents()

    def test_search_battery_top_is_power_and_network(self):
        self._show_settings()
        self._search("battery")
        self.assertEqual(self._viewport_titles()[0], "Power & network")

    def test_search_keyboard_top_is_keyboard_shortcuts(self):
        self._show_settings()
        self._search("keyboard")
        self.assertEqual(self._viewport_titles()[0],
                         "Keyboard shortcuts")

    def test_search_connect_top_is_how_vyuk_thinks(self):
        self._show_settings()
        self._search("connect")
        self.assertEqual(self._viewport_titles()[0], "How VYUK thinks")

    def test_search_theme_mic_voice_tops_unchanged(self):
        self._show_settings()
        for query, want in (("theme", "Appearance"),
                            ("mic", "Microphone"),
                            ("voice", "Voice")):
            self._search(query)
            self.assertEqual(self._viewport_titles()[0], want,
                             f"query {query!r}")
            self._search("")

    def test_search_keeps_the_visible_set(self):
        # Ranking moves cards; the matched set for each query is
        # the pre-fix set (measured in the field-check repro).
        self._show_settings()
        self._search("battery")
        self.assertEqual(self._visible_set(),
                         {"Notices", "Suggestions", "Presence",
                          "Power & network"})
        self._search("keyboard")
        self.assertEqual(self._visible_set(),
                         {"How VYUK thinks", "Keyboard shortcuts"})
        self._search("connect")
        # UI Phase 4 (D-P4-31): the Voice card carries the effect
        # line "Takes effect the next time you connect." — card
        # text, so it matches "connect" like any other card text.
        # UI Phase 5 (2026-11-27): the Home hub card's text
        # ("Connect a Home Assistant instance…") matches
        # "connect" the same way.
        self.assertEqual(self._visible_set(),
                         {"Sounds", "Permissions", "Screen watching",
                          "Browser control", "Notices",
                          "How VYUK thinks", "Your world", "Presence",
                          "Local brain", "Help", "Power & network",
                          "Voice", "Home hub"})

    def test_search_hides_group_headers_while_ranked(self):
        self._show_settings()
        self._search("battery")
        for hdr in self.window._settings_group_headers:
            self.assertFalse(hdr.isVisible(),
                             f"header {hdr.text()!r} shows over ranked "
                             "results")

    def test_clear_restores_page_and_card_homes(self):
        self._show_settings()
        initial = self._viewport_titles()
        self._search("battery")
        self.assertNotEqual(self._viewport_titles(), initial)
        self._search("")
        self.assertEqual(self._viewport_titles(), initial)
        for hdr in self.window._settings_group_headers:
            self.assertTrue(hdr.isVisible(),
                            f"header {hdr.text()!r} stays hidden after "
                            "clearing the search")
        # Every card is back in its recorded home layout.
        for card, (lay, _idx) in (
                self.window._settings_card_homes.items()):
            self.assertIs(card.parentWidget().layout(), lay)

    def test_top_task_language_lands_local_brain_in_view(self):
        self._show_settings()
        self.window._settings_top_tasks["Language"].click()
        from PySide6.QtTest import QTest
        QTest.qWait(1200)
        self.app.processEvents()
        self.assertTrue(self.window._more_expanded,
                        "Advanced drawer did not expand")
        self.assertIn("Local brain", self._viewport_titles())

    def test_top_tasks_mic_voice_connect_theme_still_land(self):
        self._show_settings()
        for label, want in (("Mic", "Microphone"), ("Voice", "Voice"),
                            ("Connect", "How VYUK thinks"),
                            ("Theme", "Appearance")):
            self.window._settings_top_tasks[label].click()
            from PySide6.QtTest import QTest
            QTest.qWait(1200)
            self.app.processEvents()
            self.assertIn(want, self._viewport_titles(),
                          f"top task {label!r}")


if __name__ == "__main__":
    unittest.main()
