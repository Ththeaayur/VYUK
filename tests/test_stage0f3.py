"""Stage 0 features batch 3 (Group F, 2026-11-22): money — items
24 (budget alerts — budget_envelope exists, the over-budget
warning is missing), 25 (price-watch alerts — price_watch
exists, the proactive alert is missing), 26 (portfolio tracker —
missing entirely), 27 (subscription renewal alerts —
subscription_audit exists, renewal dates + warnings are missing).

This suite pins the batch, so it can never regress silently:

- Both new tools are registered, classified (effectful, FREE by
  design, app-state), labelled, grouped (Everyday skills),
  synonymed, and in the evidence table + snapshot with class
  free. Neither is a read or confirm-gated.
- Both are owner-only by construction (L-24): they join
  _OWNER_ONLY_DISPATCHED, and _is_read_action names the read
  half (check_money_alerts always; portfolio list/total) — so a
  money answer is refused in an unproven live-origin session
  (the funnel leg lives in the repro probe; this suite pins the
  gate sets the funnel reads).
- The alert check fires each kind once and dedupes: a standing
  condition never nags; a moved number re-fires; the ledger is
  written atomically and re-read before the claim (A3-atomic).
  A failed price fetch is named honestly, never hidden and never
  counted as an alert.
- The briefing's money section shows standing alerts from
  last-known prices (no fetch), appears only when there are
  lines, and survives a money-read failure with one honest line.
- The portfolio adds, accumulates, lists and totals from real
  holdings + recorded prices; a holding with no recorded price
  is unvalued with the plain line that live quotes are off —
  never a fabricated price. Every add re-reads before claiming
  (A1-store).
- subscription_audit gains renewal_day (1–31): stored values
  become JSON {"monthly": X, "renewal_day": D}, legacy plain
  numbers still read, 'list' shows the next renewal date, and
  _next_renewal clamps to the month's last day.
- Census pins: 764 tools / 384 read / 380 effectful / 169
  confirm-gated / params 2102 / grouped 761 / closed 763 /
  already-verified 314 / §2c rows 380 / labels 689 /
  productivity 148 / desktop offered 71 / assistant offered 73.

Stdlib unittest only. All money-store writes go through a temp
MemoryStore + temp DATA_DIR (the _store seam); all price fetches
go through a stubbed _fetch. No data/ writes, no network.
"""

import calendar
import datetime
import json
import re
import tempfile
import unittest
from pathlib import Path

from core import money_alerts as _ma  # noqa: E402
import core.morning_briefing as _mb  # noqa: E402
import core.skills_money_budget as _budget  # noqa: E402
import core.skills_web6_watch as _watch  # noqa: E402
from core.funnel import tools_gate as _gate  # noqa: E402
from core.funnel import toolbox_tokens as _tokens  # noqa: E402
from core.funnel.tool_classification import (  # noqa: E402
    _CONFIRM_GATE_TOOLS,
    _FREE_BY_DESIGN,
    _READ_ONLY_TOOLS,
)
from core.funnel.tool_profiles import DESKTOP_TOOL_PRIORITY  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core import tool_labels as _labels  # noqa: E402
from core.catalogue_groups_data import GROUP_MEMBERS  # noqa: E402

_TZ = datetime.datetime.now().astimezone().tzinfo
_TREE = Path(__file__).resolve().parent.parent
_PAGE_80 = b"<html><body><p>Headphones $80.00 today</p></body></html>"
_PAGE_120 = b"<html><body><p>Headphones $120.00 today</p></body></html>"
_URL = "https://example.com/headphones"


class _TempMoney:
    """Temp MemoryStore + temp DATA_DIR for the money legs."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        from core.memory.memory import MemoryStore  # noqa: E402
        self.store = MemoryStore(self.dir / "assistant.db")
        import core.skills_money_helpers as helpers  # noqa: E402
        import core.config as config_mod  # noqa: E402
        self._old = (helpers._store, _budget._store,
                     config_mod.DATA_DIR, _watch.DATA_DIR)
        helpers._store = lambda: self.store
        _budget._store = lambda: self.store
        config_mod.DATA_DIR = self.dir
        _watch.DATA_DIR = self.dir
        return self

    def __exit__(self, *exc):
        import core.skills_money_helpers as helpers  # noqa: E402
        import core.config as config_mod  # noqa: E402
        (helpers._store, _budget._store,
         config_mod.DATA_DIR, _watch.DATA_DIR) = self._old
        self.tmp.cleanup()
        return False


def _watch_entry(target):
    return {"target": target, "added_at": "",
            "last_price": None, "last_display": "",
            "last_checked": ""}


def _envelope(name, budget, spent):
    return json.dumps({"budget": float(budget), "spent": float(spent)})


class TestRegistration(unittest.TestCase):
    """Both tools registered, classified, labelled, grouped."""

    @classmethod
    def setUpClass(cls):
        cls.decls = {d["name"]: d for d in tools.get_declarations()}

    def test_both_registered_with_handlers(self):
        self.assertIn("check_money_alerts", self.decls)
        self.assertIn("portfolio_tracker", self.decls)
        self.assertTrue(callable(_ma.check_money_alerts))
        self.assertTrue(callable(_budget.portfolio_tracker))

    def test_both_free_app_state_not_read_not_confirm(self):
        for name in ("check_money_alerts", "portfolio_tracker"):
            with self.subTest(tool=name):
                self.assertEqual(_FREE_BY_DESIGN.get(name), "app-state")
                self.assertNotIn(name, _READ_ONLY_TOOLS)
                self.assertNotIn(name, _CONFIRM_GATE_TOOLS)

    def test_both_owner_only_dispatched(self):
        for name in ("check_money_alerts", "portfolio_tracker"):
            self.assertIn(name, _gate._OWNER_ONLY_DISPATCHED)

    def test_is_read_action_branches(self):
        from core.funnel.tool_classification import _is_read_action
        self.assertTrue(_is_read_action("check_money_alerts", {}))
        self.assertTrue(_is_read_action("portfolio_tracker",
                                        {"action": "list"}))
        self.assertTrue(_is_read_action("portfolio_tracker",
                                        {"action": "total"}))
        self.assertFalse(_is_read_action("portfolio_tracker",
                                         {"action": "add"}))
        self.assertFalse(_is_read_action("portfolio_tracker",
                                         {"action": "remove"}))

    def test_desktop_priority_59_both_resident(self):
        self.assertEqual(len(DESKTOP_TOOL_PRIORITY), 59)
        self.assertIn("check_money_alerts", DESKTOP_TOOL_PRIORITY)
        self.assertIn("portfolio_tracker", DESKTOP_TOOL_PRIORITY)

    def test_synonyms_labels_groups(self):
        for name in ("check_money_alerts", "portfolio_tracker"):
            with self.subTest(tool=name):
                self.assertIn(name, _tokens.TOOL_SYNONYMS)
                self.assertIn(name, _labels.TOOL_FRIENDLY_NAMES)
                self.assertIn(name, GROUP_MEMBERS["Everyday skills"])

    def test_portfolio_six_properties_check_no_params(self):
        props = self.decls["portfolio_tracker"]["parameters"]["properties"]
        self.assertEqual(
            set(props),
            {"action", "symbol", "quantity", "buy_price", "price",
             "currency"})
        self.assertEqual(
            self.decls["check_money_alerts"]["parameters"]["properties"],
            {})

    def test_evidence_free_snapshot_app_state(self):
        ev = json.loads(
            (_TREE / "tests" / "_classification_evidence.json")
            .read_text(encoding="utf-8"))
        snap = json.loads(
            (_TREE / "tests" / "_classification_snapshot.json")
            .read_text(encoding="utf-8"))
        for name in ("check_money_alerts", "portfolio_tracker"):
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
        self.assertIn("| `check_money_alerts` | A3-atomic", section)
        self.assertIn("| `portfolio_tracker` | A1-store", section)


class TestBudgetAlerts(unittest.TestCase):
    """Item 24: the over-budget warning, firing once."""

    def test_over_budget_line_only_when_over(self):
        entries = [{"key": "Groceries", "budget": 100.0, "spent": 150.0},
                   {"key": "Fuel", "budget": 50.0, "spent": 50.0}]
        lines = _ma._over_budget_lines(entries)
        self.assertEqual(len(lines), 1)
        self.assertIn("Groceries", lines[0])
        self.assertIn("50.00 OVER budget", lines[0])

    def test_check_fires_once_then_dedupes_then_refires(self):
        with _TempMoney() as tmp:
            tmp.store.save_fact("envelopes", "Groceries",
                                _envelope("Groceries", 100, 150))
            first = _ma.check_money_alerts()
            second = _ma.check_money_alerts()
            tmp.store.save_fact("envelopes", "Groceries",
                                _envelope("Groceries", 100, 160))
            third = _ma.check_money_alerts()
            self.assertIn("Money alerts (1)", first)
            self.assertIn("OVER budget", first)
            self.assertTrue(second.startswith("No money alerts"))
            self.assertIn("OVER budget", third)

    def test_back_under_budget_clears_the_ledger(self):
        with _TempMoney() as tmp:
            tmp.store.save_fact("envelopes", "Groceries",
                                _envelope("Groceries", 100, 150))
            _ma.check_money_alerts()
            tmp.store.save_fact("envelopes", "Groceries",
                                _envelope("Groceries", 100, 90))
            _ma.check_money_alerts()
            ledger = _ma._load_alerts(path=tmp.dir / "money_alerts.json")
            self.assertNotIn("Groceries", ledger["budget"])

    def test_ledger_round_trip_and_corrupt_reads_empty(self):
        with _TempMoney() as tmp:
            path = tmp.dir / "money_alerts.json"
            self.assertTrue(_ma._save_alerts(
                {"budget": {}, "price": {}, "subscription": {}},
                path=path))
            self.assertEqual(
                _ma._load_alerts(path=path),
                {"budget": {}, "price": {}, "subscription": {}})
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(
                _ma._load_alerts(path=path),
                {"budget": {}, "price": {}, "subscription": {}})

    def test_quiet_money_reports_no_alerts(self):
        with _TempMoney():
            self.assertTrue(
                _ma.check_money_alerts().startswith("No money alerts"))


class TestPriceAlerts(unittest.TestCase):
    """Item 25: the price-watch alert, through the fetch seam."""

    def test_standing_line_from_last_known_price(self):
        watches = {_URL: {"target": 100.0, "last_price": 80.0,
                          "last_display": "$80.00",
                          "last_checked": "2026-11-22T08:00:00"}}
        lines = _ma._price_lines(watches)
        self.assertEqual(len(lines), 1)
        self.assertIn("$80.00", lines[0])
        self.assertIn("2026-11-22T08:00:00", lines[0])

    def test_standing_line_skips_above_target_and_no_target(self):
        watches = {
            _URL: {"target": 100.0, "last_price": 120.0,
                   "last_display": "$120.00", "last_checked": "x"},
            "https://example.com/other": {"target": None,
                                          "last_price": 5.0,
                                          "last_display": "$5.00",
                                          "last_checked": "x"},
            "https://example.com/third": {"target": 10.0,
                                          "last_price": None,
                                          "last_display": "",
                                          "last_checked": ""},
        }
        self.assertEqual(_ma._price_lines(watches), [])

    def test_check_fires_at_target_once_not_above(self):
        with _TempMoney():
            _watch._save_prices({_URL: _watch_entry(100.0)})
            old = _ma._fetch
            try:
                _ma._fetch = lambda u, cap: (_PAGE_80, None, "")
                first = _ma.check_money_alerts()
                second = _ma.check_money_alerts()
                _ma._fetch = lambda u, cap: (_PAGE_120, None, "")
                third = _ma.check_money_alerts()
            finally:
                _ma._fetch = old
            self.assertIn("Price alert", first)
            self.assertIn("80.00", first)
            self.assertTrue(second.startswith("No money alerts"))
            self.assertNotIn("Price alert", third)
            self.assertEqual(_watch._load_prices()[_URL]["last_price"],
                             120.0)

    def test_fetch_failure_named_honestly_never_an_alert(self):
        with _TempMoney():
            _watch._save_prices({_URL: _watch_entry(100.0)})
            old = _ma._fetch
            try:
                _ma._fetch = lambda u, cap: (None, None, "boom")
                out = _ma.check_money_alerts()
            finally:
                _ma._fetch = old
            self.assertIn("Couldn't fetch", out)
            self.assertNotIn("Price alert", out)

    def test_no_price_on_page_named_honestly(self):
        with _TempMoney():
            _watch._save_prices({_URL: _watch_entry(100.0)})
            old = _ma._fetch
            try:
                _ma._fetch = lambda u, cap: (
                    b"<html><body><p>no prices here</p></body></html>",
                    None, "")
                out = _ma.check_money_alerts()
            finally:
                _ma._fetch = old
            self.assertIn("No price found", out)
            self.assertNotIn("Price alert", out)


class TestPortfolio(unittest.TestCase):
    """Item 26: the portfolio tracker."""

    def test_add_list_total_from_real_holdings(self):
        with _TempMoney():
            add1 = _budget.portfolio_tracker(
                action="add", symbol="aapl", quantity=10,
                buy_price=150, price=200)
            add2 = _budget.portfolio_tracker(
                action="add", symbol="MSFT", quantity=5, buy_price=300)
            listed = _budget.portfolio_tracker(action="list")
            total = _budget.portfolio_tracker(action="total")
            self.assertIn("Added 'AAPL'", add1)
            self.assertIn("2,000.00", add1)
            self.assertIn("Added 'MSFT'", add2)
            self.assertIn("AAPL — 10 held", listed)
            self.assertIn("no recorded price — live quotes are off",
                          listed)
            self.assertIn("Invested: 3,000.00", total)
            self.assertIn("Current value: 2,000.00", total)
            self.assertIn("Unvalued: MSFT", total)

    def test_add_accumulates_and_keeps_old_prices(self):
        with _TempMoney():
            _budget.portfolio_tracker(
                action="add", symbol="AAPL", quantity=10,
                buy_price=150, price=200)
            again = _budget.portfolio_tracker(
                action="add", symbol="aapl", quantity=5)
            listed = _budget.portfolio_tracker(action="list")
            self.assertIn("Added to 'AAPL': 15 held", again)
            self.assertIn("AAPL — 15 held", listed)
            self.assertIn("value 3,000.00", listed)

    def test_remove_and_unknown_remove(self):
        with _TempMoney():
            _budget.portfolio_tracker(
                action="add", symbol="AAPL", quantity=10, buy_price=150)
            removed = _budget.portfolio_tracker(
                action="remove", symbol="aapl")
            gone = _budget.portfolio_tracker(
                action="remove", symbol="AAPL")
            self.assertIn("Removed 'AAPL'", removed)
            self.assertIn("There's no holding called 'AAPL'", gone)

    def test_empty_list_and_total(self):
        with _TempMoney():
            self.assertIn("No holdings",
                          _budget.portfolio_tracker(action="list"))
            self.assertIn("No holdings",
                          _budget.portfolio_tracker(action="total"))

    def test_bad_quantity_and_bad_action(self):
        with _TempMoney():
            self.assertIn("more than zero",
                          _budget.portfolio_tracker(
                              action="add", symbol="AAPL", quantity=0))
            self.assertIn("Portfolio actions are",
                          _budget.portfolio_tracker(action="hold"))

    def test_broken_holding_counted_not_fatal(self):
        with _TempMoney() as tmp:
            tmp.store.save_fact("portfolio", "BAD", "not-json{")
            entries, broken = _budget._load_portfolio()
            self.assertEqual((entries, broken), ([], 1))


class TestRenewals(unittest.TestCase):
    """Item 27: subscription renewal dates + warnings."""

    def test_next_renewal_clamps_rolls_rejects(self):
        nxt = _budget._next_renewal
        self.assertEqual(nxt(31, datetime.date(2026, 2, 15)),
                         datetime.date(2026, 2, 28))
        self.assertEqual(nxt(5, datetime.date(2026, 11, 22)),
                         datetime.date(2026, 12, 5))
        self.assertEqual(nxt(25, datetime.date(2026, 11, 22)),
                         datetime.date(2026, 11, 25))
        self.assertEqual(nxt(22, datetime.date(2026, 11, 22)),
                         datetime.date(2026, 11, 22))
        self.assertIsNone(nxt(0, datetime.date(2026, 11, 22)))
        self.assertIsNone(nxt(32, datetime.date(2026, 11, 22)))
        self.assertIsNone(nxt("xx", datetime.date(2026, 11, 22)))

    def test_add_with_renewal_day_stores_json_list_shows_date(self):
        with _TempMoney() as tmp:
            today = datetime.date.today()
            day = today.day + 3
            if day > calendar.monthrange(today.year, today.month)[1]:
                day = today.day
            added = _budget.subscription_audit(
                action="add", name="Netflix", monthly=15,
                renewal_day=day)
            nxt = _budget._next_renewal(day, today)
            self.assertIn("Renews " + nxt.isoformat(), added)
            stored = tmp.store.get_all_facts()
            value = [f["value"] for f in stored
                     if f["category"] == "subscriptions"][0]
            self.assertEqual(json.loads(value),
                             {"monthly": 15.0, "renewal_day": day})
            listed = _budget.subscription_audit(action="list")
            self.assertIn("renews " + nxt.isoformat(), listed)

    def test_invalid_renewal_day_refused(self):
        with _TempMoney():
            self.assertIn("1 to 31",
                          _budget.subscription_audit(
                              action="add", name="X", monthly=5,
                              renewal_day=32))
            self.assertIn("1 to 31",
                          _budget.subscription_audit(
                              action="add", name="X", monthly=5,
                              renewal_day=-5))
            # renewal_day 0 is the default "no day given": the
            # add succeeds and carries no renewal date.
            added = _budget.subscription_audit(
                action="add", name="X", monthly=5, renewal_day=0)
            self.assertIn("Added 'X'", added)
            self.assertNotIn("Renews", added)

    def test_legacy_plain_number_still_reads(self):
        with _TempMoney() as tmp:
            tmp.store.save_fact("subscriptions", "OldMag", "9.99")
            entries, broken = _budget._load_subscriptions()
            self.assertEqual(broken, 0)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["monthly"], 9.99)
            self.assertIsNone(entries[0]["renewal_day"])
            self.assertNotIn(
                "renews",
                _budget.subscription_audit(action="list"))

    def test_renewal_alert_fires_once_in_window(self):
        with _TempMoney():
            today = datetime.date.today()
            day = today.day + 3
            if day > calendar.monthrange(today.year, today.month)[1]:
                day = today.day
            _budget.subscription_audit(
                action="add", name="Netflix", monthly=15,
                renewal_day=day)
            first = _ma.check_money_alerts()
            second = _ma.check_money_alerts()
            self.assertIn("Renewal alert", first)
            self.assertIn("Netflix", first)
            self.assertTrue(second.startswith("No money alerts"))


class TestBriefing(unittest.TestCase):
    """The morning briefing's money section."""

    def _now(self):
        return datetime.datetime(2026, 11, 22, 8, 0, tzinfo=_TZ)

    def test_section_appears_with_lines_absent_without(self):
        report = {"scope_note": "", "partial": False}
        with_lines = _mb.render_morning_briefing(
            self._now(), [], report,
            money_lines=["Budget alert — 'X' is OVER."])
        without = _mb.render_morning_briefing(self._now(), [], report)
        self.assertIn("Money alerts:", with_lines)
        self.assertIn("Budget alert", with_lines)
        self.assertNotIn("Money alerts", without)

    def test_section_sits_with_events_too(self):
        report = {"scope_note": "", "partial": False}
        event = {"summary": "Dentist",
                 "start": {"dateTime": "2026-11-22T10:00:00+05:30"},
                 "end": {"dateTime": "2026-11-22T11:00:00+05:30"}}
        out = _mb.render_morning_briefing(
            self._now(), [event], report,
            money_lines=["Budget alert — 'X' is OVER."])
        self.assertIn("Dentist", out)
        self.assertIn("Money alerts:", out)

    def test_handler_passes_standing_alerts(self):
        with _TempMoney() as tmp:
            tmp.store.save_fact("envelopes", "Groceries",
                                _envelope("Groceries", 100, 150))
            old = _mb.todays_schedule_data
            try:
                _mb.todays_schedule_data = lambda: {
                    "ok": True, "events": [],
                    "report": {"scope_note": "", "partial": False}}
                out = _mb.get_morning_briefing()
            finally:
                _mb.todays_schedule_data = old
            self.assertIn("Money alerts:", out)
            self.assertIn("Groceries", out)
            self.assertIn("OVER budget", out)

    def test_handler_survives_money_failure(self):
        def _boom(now=None):
            raise RuntimeError("boom")

        old = _mb.todays_schedule_data
        old_lines = _ma.money_alert_lines_for_briefing
        try:
            _mb.todays_schedule_data = lambda: {
                "ok": True, "events": [],
                "report": {"scope_note": "", "partial": False}}
            _ma.money_alert_lines_for_briefing = _boom
            out = _mb.get_morning_briefing()
        finally:
            _mb.todays_schedule_data = old
            _ma.money_alert_lines_for_briefing = old_lines
        self.assertIn("Money alerts couldn't be read just now.", out)


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
            (764, 384, 380, 169, 314))

    def test_params_grouped_closed(self):
        total_params = sum(
            len(d.get("parameters", {}).get("properties", {}))
            for d in self.decls.values())
        grouped = sum(len(v) for v in GROUP_MEMBERS.values())
        closed = sum(
            1 for d in self.decls.values()
            if d.get("parameters", {}).get("additionalProperties")
            is False)
        self.assertEqual(total_params, 2102)
        self.assertEqual(grouped, 761)
        self.assertEqual(closed, 763)

    def test_section_2c_rows_labels_productivity(self):
        self.assertEqual(len(self.rows_2c), 380)
        self.assertEqual(len(set(self.rows_2c)), 380)
        self.assertEqual(len(_labels.TOOL_FRIENDLY_NAMES), 689)
        self.assertEqual(self.counts.get("productivity"), 148)

    def test_profiles(self):
        from core.funnel import tool_profiles as profiles  # noqa: E402
        self.assertEqual(
            len(profiles.profile_declarations(tools, "desktop")), 71)
        self.assertEqual(
            len(profiles.profile_declarations(tools, "assistant")), 73)


if __name__ == "__main__":
    unittest.main()
