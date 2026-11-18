"""RET-3 (2026-11-15): the "Today" safety surface.

One GUI panel (gui/window_today.py): today's actions from the
shared chain, today's spend from the cost ledger (totals and
per-brain aggregates only), the confirmations asked today from
the funnel decision log, and the kill-chain state — plus the
read-only CostLedger.spend_by_brain(day) it reads through. No
new tool, census unchanged.

The tests pin the same shapes as gap_ret3.py / prove_ret3.py:
spend totals match the ledger methods exactly; no prompt or
response text, no secret, no full path ever renders (adversarial
fixtures); the coverage statement names exactly the sources
read; a per-tool-ledger action is not presented as covered; the
confirmations section appears only when a source carries
records; undo is offered only for pending_undo_ids(); a spent
undo is not offered; no single "undo routine" button.

The gui modules import voice_assistant.core.* (the shipped
spelling), so the alias is planted first (the test_gaps27.py
pattern). Monkeypatches target the voice_assistant.* module
object — the identity window_today actually imports — not the
top-level gui.* spelling, which is a different module object
for the same file. All fixtures live in temp dirs; the real
data/ is never touched (the kill-chain test configures a temp
dir and resets afterwards).
"""

import datetime
import sys
import tempfile
import types
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

TREE = Path(__file__).resolve().parent.parent
_pkg = types.ModuleType("voice_assistant")
_pkg.__path__ = [str(TREE)]
sys.modules.setdefault("voice_assistant", _pkg)

import gui.window_today as today  # noqa: E402
import voice_assistant.gui.window_activity as act_va  # noqa: E402
import voice_assistant.core.funnel.kill_chain as kc_va  # noqa: E402
# The record family MUST be the voice_assistant.* identity:
# core.record_family (top-level) is a different module object
# with its own in-memory store, and pending_undo_ids() reads
# the voice_assistant one (via the facade's relative import).
import voice_assistant.core.record_family as rf_va  # noqa: E402
from core.cost_ledger import CostLedger  # noqa: E402

KOL = ZoneInfo("Asia/Kolkata")
SECRET = "hunter2-sk-live-SECRET999"
PROMPT_MARKER = "PROMPT_MARKER_7f3a9c1e"
PATH_MARKER = "/home/aayur/secret/vault.txt"


def _noon(d):
    return datetime.datetime(d.year, d.month, d.day, 12, 0,
                             tzinfo=KOL).timestamp()


def _ts(d, hour=12):
    return datetime.datetime(d.year, d.month, d.day, hour, 0).isoformat(
        timespec="seconds")


class _Ret3Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.today = datetime.datetime.now(KOL).date()
        self.yesterday = self.today - datetime.timedelta(days=1)
        # --- cost ledger: today 0.01+0.02+0.03, yesterday 9.99,
        # local brain 5.00 (both must be excluded).
        self.db_path = self.dir / "cost.db"
        led = CostLedger(db_path=self.db_path)
        led.record_turn("live", "model-a", 10, 5, "exact",
                        cost_usd=0.01, ts=_noon(self.today))
        led.record_turn("cloud_text", "model-b", 10, 5, "exact",
                        cost_usd=0.02, ts=_noon(self.today))
        led.record_turn("live", "model-a", 10, 5, "exact",
                        cost_usd=0.03, ts=_noon(self.today))
        led.record_turn("live", "model-a", 10, 5, "exact",
                        cost_usd=9.99, ts=_noon(self.yesterday))
        led.record_turn("local", "model-local", 10, 5, "exact",
                        cost_usd=5.00, ts=_noon(self.today))
        led.close()
        # --- activity.log: 2 decisions today, 1 yesterday, 1
        # today carrying a secret arg + a path in the reason.
        self.log_path = self.dir / "activity.log"
        self.log_path.write_text(
            "%s | confirm_gate | send_email: APPROVED [req r1] "
            "(user confirmed at popup)\n" % _ts(self.today) +
            "%s | confirm_gate | delete_file: DENIED [req r2] "
            "(money-guard)\n" % _ts(self.today) +
            "%s | confirm_gate | send_email: APPROVED [req r0] "
            "(user confirmed at popup)\n" % _ts(self.yesterday) +
            "%s | confirm_gate | open_file: APPROVED [req r3] "
            "(args={'password': '%s'} file %s)\n"
            % (_ts(self.today, 13), SECRET, PATH_MARKER),
            encoding="utf-8")
        # --- chain feed (monkeypatched): one normal today entry,
        # one with a path in the title, one carrying a prompt in
        # an extra field the surface must never read, one
        # yesterday entry that must be filtered out.
        self.feed = [
            {"action_id": "e1", "title": "Ran backup",
             "when": _ts(self.today) + "Z", "undoable": False,
             "reversible": None},
            {"action_id": "e2", "title": "Opened " + PATH_MARKER,
             "when": _ts(self.today) + "Z", "undoable": False,
             "reversible": None},
            {"action_id": "e3", "title": "Saved note",
             "when": _ts(self.today) + "Z", "undoable": False,
             "reversible": None, "prompt": PROMPT_MARKER},
            {"action_id": "e4", "title": "Old action",
             "when": _ts(self.yesterday) + "Z", "undoable": False,
             "reversible": None},
        ]
        self._orig_feed = act_va.activity_feed
        self._orig_pending = act_va.pending_undo_ids
        self._orig_unified = act_va.unified_feed
        act_va.activity_feed = (
            lambda limit=50: {"available": True,
                              "entries": list(self.feed),
                              "error": None})
        act_va.pending_undo_ids = lambda: ["undo:alpha", "undo:beta"]
        act_va.unified_feed = (
            lambda limit=50, include=None: {
                "available": True,
                "entries": list(self.feed) + [
                    {"action_id": "u1",
                     "title": "Per-tool ledger action",
                     "when": _ts(self.today) + "Z",
                     "undoable": False, "reversible": None,
                     "source": "ledger", "store": "tool_x"}],
                "error": None})

    def tearDown(self):
        act_va.activity_feed = self._orig_feed
        act_va.pending_undo_ids = self._orig_pending
        act_va.unified_feed = self._orig_unified
        kc_va.reset_configuration()
        self.tmp.cleanup()

    def _view(self):
        return today.today_view(now=datetime.datetime.now(),
                                activity_path=self.log_path,
                                db_path=self.db_path)

    # --- redaction ------------------------------------------------------
    def test_redact_path_to_basename(self):
        self.assertEqual(today.redact_text("Opened " + PATH_MARKER),
                         "Opened vault.txt")
        self.assertEqual(today.redact_text("C:\\Users\\aayur\\x.txt"),
                         "x.txt")

    def test_redact_credential_key_value(self):
        out = today.redact_text(
            "args={'password': '%s'} token=abc123" % SECRET)
        self.assertNotIn(SECRET, out)
        self.assertNotIn("hunter2", out)
        self.assertIn("password=[redacted]", out)

    def test_redact_bearer_and_sk_tokens(self):
        out = today.redact_text(
            "Bearer abc.def.ghi and sk-live-SECRET999 here")
        self.assertNotIn("abc.def.ghi", out)
        self.assertNotIn("sk-live-SECRET999", out)

    def test_redact_leaves_plain_numbers_and_urls(self):
        text = "5/7 done, see https://example.com/a/b for more"
        self.assertEqual(today.redact_text(text), text)

    def test_redact_never_raises_on_none(self):
        self.assertEqual(today.redact_text(None), "")
        self.assertEqual(today.redact_text(""), "")

    # --- spend_by_brain ---------------------------------------------------
    def test_spend_by_brain_today_only(self):
        led = CostLedger(db_path=self.db_path)
        try:
            rows = led.spend_by_brain(day=self.today.isoformat())
        finally:
            led.close()
        self.assertEqual(
            {(r["brain"], r["model"]): (r["turns"], r["cost_usd"])
             for r in rows},
            {("live", "model-a"): (2, 0.04),
             ("cloud_text", "model-b"): (1, 0.02)})
        self.assertAlmostEqual(
            sum(r["cost_usd"] for r in rows), 0.06, places=9)

    def test_spend_by_brain_missing_table_empty(self):
        led = CostLedger(db_path=self.dir / "fresh.db")
        try:
            self.assertEqual(
                led.spend_by_brain(day=self.today.isoformat()), [])
        finally:
            led.close()

    def test_spend_by_brain_grouping(self):
        led = CostLedger(db_path=self.db_path)
        try:
            self.assertAlmostEqual(
                led.spend_day(day=self.today.isoformat()), 0.06,
                places=9)
        finally:
            led.close()

    # --- today_date ---------------------------------------------------------
    def test_today_date_is_kolkata(self):
        self.assertEqual(today.today_date(),
                         datetime.datetime.now(KOL).date())

    # --- confirmations ------------------------------------------------------
    def test_confirmations_today_only(self):
        result = today.read_confirmations_today(
            activity_path=self.log_path)
        entries = result["entries"]
        self.assertTrue(result["available"])
        self.assertEqual(
            {(e["tool"], e["verdict"]) for e in entries},
            {("send_email", "APPROVED"), ("delete_file", "DENIED"),
             ("open_file", "APPROVED")})

    def test_confirmations_malformed_lines_skipped(self):
        bad = self.dir / "bad.log"
        bad.write_text(
            "not a decision line\n"
            "%s | confirm_gate | send_email: APPROVED [req r9] "
            "(ok)\n" % _ts(self.today) +
            "%s | confirm_gate | garbage without verdict\n"
            % _ts(self.today),
            encoding="utf-8")
        entries = today.read_confirmations_today(
            activity_path=bad)["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["tool"], "send_email")

    def test_confirmations_missing_file_empty(self):
        result = today.read_confirmations_today(
            activity_path=self.dir / "nope.log")
        self.assertFalse(result["available"])
        self.assertEqual(result["entries"], [])

    # --- view / render ------------------------------------------------------
    def test_spend_totals_match_ledger_methods(self):
        view = self._view()
        spend = view["spend"]
        self.assertTrue(spend["available"])
        self.assertAlmostEqual(spend["today_usd"], 0.06, places=9)
        self.assertAlmostEqual(
            sum(r["cost_usd"] for r in spend["by_brain"]), 0.06,
            places=9)

    def test_coverage_names_exactly_the_sources_read(self):
        view = self._view()
        self.assertEqual(list(view["sources"]),
                         list(today.COVERAGE_SOURCES))
        for source in today.COVERAGE_SOURCES:
            self.assertIn(source, view["coverage"])

    def test_per_tool_ledger_action_not_presented_as_covered(self):
        rendered = today.render_today_text(self._view())
        self.assertNotIn("Per-tool ledger action", rendered)
        self.assertIn("per-tool ledger", rendered)
        self.assertIn("M-V1/M-V4", rendered)

    def test_confirmations_present_when_records_exist(self):
        view = self._view()
        self.assertTrue(view["confirmations"]["available"])
        self.assertEqual(len(view["confirmations"]["entries"]), 3)

    def test_confirmations_omitted_when_no_records(self):
        empty = self.dir / "empty.log"
        empty.write_text("", encoding="utf-8")
        view = today.today_view(now=datetime.datetime.now(),
                                activity_path=empty,
                                db_path=self.db_path)
        rendered = today.render_today_text(view)
        self.assertNotIn("Confirmations asked today", rendered)
        self.assertIn("section omitted", rendered)

    def test_no_secret_or_full_path_renders(self):
        rendered = today.render_today_text(self._view())
        self.assertNotIn(SECRET, rendered)
        self.assertNotIn("hunter2", rendered)
        self.assertNotIn(PATH_MARKER, rendered)
        self.assertIn("vault.txt", rendered)

    def test_no_prompt_text_renders(self):
        rendered = today.render_today_text(self._view())
        self.assertNotIn(PROMPT_MARKER, rendered)

    def test_undo_offer_only_pending_ids(self):
        self.assertEqual(today.undo_offer(self._view()),
                         ["undo:alpha", "undo:beta"])

    def test_spent_undo_not_offered(self):
        # The real pending_undo_ids path (no monkeypatch): an id
        # is offered while pending and gone once spent.
        act_va.pending_undo_ids = self._orig_pending
        rf_va.register_undo("scratch:keep", lambda: {"ok": True})
        rf_va.register_undo("scratch:spent", lambda: {"ok": True})
        offer_before = today.undo_offer(self._view())
        self.assertIn("scratch:keep", offer_before)
        self.assertIn("scratch:spent", offer_before)
        rf_va.execute_undo("scratch:spent")
        offer = today.undo_offer(self._view())
        self.assertIn("scratch:keep", offer)
        self.assertNotIn("scratch:spent", offer)

    def test_no_undo_routine_button(self):
        rendered = today.render_today_text(self._view())
        self.assertNotIn("undo routine", rendered.lower())
        self.assertNotIn("undo_routine", rendered)
        self.assertNotIn("routine",
                         " ".join(today.undo_offer(self._view())))

    def test_kill_chain_rung_renders(self):
        kdir = self.dir / "kc"
        kdir.mkdir()
        kc_va.configure(data_dir=kdir)
        try:
            kc_va.escalate(1, "test freeze")
            view = self._view()
            rendered = today.render_today_text(view)
        finally:
            kc_va.clear("test done")
            kc_va.reset_configuration()
        self.assertEqual(view["kill_chain"]["rung"], 1)
        self.assertIn("rung 1", rendered)

    def test_kill_chain_rung_zero_renders_when_clear(self):
        view = self._view()
        self.assertEqual(view["kill_chain"]["rung"], 0)
        self.assertIn("rung 0", today.render_today_text(view))

    # --- static guards --------------------------------------------------------
    def test_today_module_has_no_raw_sql_no_timer_no_charging(self):
        src = (TREE / "gui" / "window_today.py").read_text(
            encoding="utf-8")
        self.assertNotIn("SELECT", src)
        self.assertNotIn("sqlite3", src)
        self.assertNotIn("QTimer", src)
        for word in ("paywall", "upgrade", "billing", "charge("):
            self.assertNotIn(word, src)

    def test_facade_exports_kill_chain(self):
        from core.gui_facade import _EXPORTS
        self.assertIn("kill_chain", _EXPORTS)

    def test_surfaced_from_settings(self):
        src = (TREE / "gui" / "window_settings_advcards.py").read_text(
            encoding="utf-8")
        self.assertIn("Today", src)
        self.assertIn("_open_today", src)
        self.assertIn("build_today_dialog", src)

    def test_pf10_bundle_unchanged(self):
        from gui.window_trust import build_report_bundle
        bundle = build_report_bundle()
        allowed = {"app", "version", "generated_at", "platform",
                   "platform_detail", "degradation", "power_policy",
                   "note", "bundle_error"}
        self.assertTrue(set(bundle) <= allowed, set(bundle))

    def test_coverage_statement_matches_readers(self):
        view = self._view()
        self.assertEqual(view["coverage"],
                         today.COVERAGE_STATEMENT)


if __name__ == "__main__":
    unittest.main()
