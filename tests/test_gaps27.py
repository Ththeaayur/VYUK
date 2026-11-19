"""GAPS-27 regression suite: review section 6.8, M-series
(memory / knowledge / ledger).

Pins: the audit merge (M-V2 — write_record routes into the
shared chain, one full record per execute(), legacy history
still readable), the shared assistant.db guard (M-V3), the
unified activity read (M-V4 — four sources, default feed
unchanged), the canonical step sequences (M-V5), the shared
store base + unified memory read with sources (M-V6), the
assistant.db backup manifest entry (M-V7), the telemetry
enable path staying OFF by default (M-V8), and the cost +
memory GUI surfaces (M-V9). No new tool: census pins
(755/382/373/164, params 2077, grouped 752, closed 754).

All behavioural checks run against tempfile stores — they
never touch the tree's data/. Module hygiene: importing
core.memory.memory constructs its module-level singleton,
whose _init_db migrates the LIVE assistant.db (the source
column); data/ is snapshotted at import (before any core
import) and restored in tearDownModule, so the suite leaves
the tree exactly as it found it.

Run from the tree root with PYTHONPATH=. :
    python -m unittest tests.test_gaps27
"""

import json
import re
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent

# The gui modules import voice_assistant.core.* (the shipped
# package name); plant the alias before importing them.
_pkg = types.ModuleType("voice_assistant")
_pkg.__path__ = [str(TREE)]
sys.modules.setdefault("voice_assistant", _pkg)

# Snapshot the tree's data/ BEFORE any core import (see the
# module docstring): the memory singleton migrates the live
# assistant.db on import.
_DATA = TREE / "data"
_SAVED = {}
for _entry in _DATA.iterdir():
    if _entry.is_file():
        _SAVED[_entry.name] = _entry.read_bytes()


def setUpModule():
    pass


def tearDownModule():
    for _entry in list(_DATA.iterdir()):
        if _entry.is_file() and _entry.name not in _SAVED:
            _entry.unlink()
    for _name, _blob in _SAVED.items():
        (_DATA / _name).write_bytes(_blob)


def _restore_data(saved):
    for _entry in list(_DATA.iterdir()):
        if _entry.is_file() and _entry.name not in saved:
            _entry.unlink()
    for _name, _blob in saved.items():
        (_DATA / _name).write_bytes(_blob)


class _DataGuard:
    """Snapshot/restore the tree's data/ around one test."""

    def __enter__(self):
        self._saved = {}
        for _entry in _DATA.iterdir():
            if _entry.is_file():
                self._saved[_entry.name] = _entry.read_bytes()
        return self

    def __exit__(self, *exc):
        _restore_data(self._saved)
        return False


from core import audit as audit_mod  # noqa: E402
from core import audit_chain as chain_mod  # noqa: E402
from core import backup as backup_mod  # noqa: E402
from core import cost_ledger as cost_mod  # noqa: E402
from core import record_family as rf_mod  # noqa: E402
from core import store_base  # noqa: E402
from core import telemetry as telemetry_mod  # noqa: E402
from core.memory.read_memory import read_memory as read_memory_fn  # noqa: E402
from core.memory.memory import MemoryStore  # noqa: E402


# ---------------------------------------------------------------- M-V2

class AuditMergeTests(unittest.TestCase):
    def test_write_record_routes_to_chain_not_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            chain_mod.configure(data_dir=tmp)
            audit_mod.configure(path=tmp / "audit.jsonl")
            try:
                self.assertTrue(audit_mod.write_record(
                    "gaps27_probe_tool", {"x": 1},
                    gate="free", kind="success"))
                self.assertFalse((tmp / "audit.jsonl").exists())
                entries = chain_mod.read(kind="audit", limit=None)
                self.assertEqual(len(entries), 1)
                data = entries[0]["data"]
                self.assertEqual(data["tool"], "gaps27_probe_tool")
                self.assertIn("taint", data)
                self.assertIn("epoch", data)
                self.assertIn("fg", data)
            finally:
                audit_mod.reset_configuration()
                chain_mod.reset_configuration()

    def test_read_recent_merges_legacy_and_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            legacy = tmp / "audit.jsonl"
            legacy.write_text(json.dumps({
                "ts": "2026-01-01T00:00:00.000",
                "tool": "gaps27_legacy_tool", "args": {},
                "gate": "free", "kind": "success",
                "ms": 1.0}) + "\n{\"ts\": \"torn\n", encoding="utf-8")
            audit_mod.configure(path=legacy)
            chain_mod.configure(data_dir=tmp)
            try:
                audit_mod.write_record("gaps27_probe_tool", {},
                                       gate="free", kind="success",
                                       ms=1.0)
                tools = [r.get("tool") for r in
                         audit_mod.read_recent(50)]
                self.assertEqual(
                    tools, ["gaps27_probe_tool", "gaps27_legacy_tool"])
                out = tmp / "export.jsonl"
                self.assertEqual(audit_mod.export_to(out), 2)
            finally:
                audit_mod.reset_configuration()
                chain_mod.reset_configuration()

    def test_one_execute_one_full_chain_entry(self):
        from core.funnel import tools_registry as tools_mod
        with _DataGuard():
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                chain_mod.configure(data_dir=tmp)
                audit_mod.configure(path=tmp / "audit.jsonl")
                rf_mod.configure(data_dir=tmp)
                try:
                    reg = object.__new__(tools_mod.ToolRegistry)
                    reg._tools = {}
                    reg.register("gaps27_probe_tool", "probe",
                                 {"type": "object", "properties": {}},
                                 lambda: {"status": "success"})
                    before = chain_mod.read(kind="audit", limit=None)
                    writes = {"n": 0}
                    orig = audit_mod.write_record

                    def _counting(*a, **k):
                        writes["n"] += 1
                        return orig(*a, **k)

                    audit_mod.write_record = _counting
                    try:
                        reg.execute("gaps27_probe_tool", {})
                    finally:
                        audit_mod.write_record = orig
                    after = chain_mod.read(kind="audit", limit=None)
                    new = after[len(before):]
                    self.assertEqual(len(new), 1)
                    self.assertEqual(writes["n"], 1)
                    self.assertTrue(
                        {"taint", "epoch", "fg"}
                        <= set(new[0]["data"].keys()))
                finally:
                    rf_mod.reset_configuration()
                    audit_mod.reset_configuration()
                    chain_mod.reset_configuration()


# ---------------------------------------------------------------- M-V3

class AssistantDbGuardTests(unittest.TestCase):
    def test_shared_predicate(self):
        self.assertTrue(store_base.is_assistant_db("/x/assistant.db"))
        self.assertTrue(store_base.is_assistant_db("assistant.db"))
        self.assertFalse(store_base.is_assistant_db("/x/myassistant.db"))
        self.assertFalse(store_base.is_assistant_db("/x/a.db"))
        self.assertEqual(store_base.refuse_assistant_db("/x/a.db"),
                         "/x/a.db")
        with self.assertRaises(ValueError) as ctx:
            store_base.refuse_assistant_db("/x/assistant.db",
                                           what="outbox")
        self.assertEqual(str(ctx.exception),
                         "the outbox never lives in assistant.db")

    def test_all_call_sites_refuse(self):
        from core.creative_rights_ledger import guard_path
        from core.developer_codebase_qa import (
            refuse_memory_store_write, CodebaseError)
        from core.developer_diagnostics import FingerprintStore
        from core.dv_serial_v577 import _refuse_memory_store
        from core.resilience.resilience_outbox import Outbox
        from core.resilience.resilience_state import (
            apply_durable_pragmas)
        from core.trust_slices import crash_consistent_check
        with self.assertRaises(ValueError):
            guard_path("/x/assistant.db")
        self.assertEqual(guard_path("/x/rights.db"), "/x/rights.db")
        with self.assertRaises(CodebaseError):
            refuse_memory_store_write("/x/assistant.db")
        with self.assertRaises(Exception):
            FingerprintStore("/x/assistant.db")
        with self.assertRaises(Exception):
            _refuse_memory_store("/x/assistant.db")
        with self.assertRaises(ValueError):
            Outbox("/x/assistant.db")
        with self.assertRaises(ValueError):
            apply_durable_pragmas("/x/assistant.db")
        self.assertFalse(
            crash_consistent_check("/x/assistant.db")["ok"])

    def test_no_copied_basename_checks_remain(self):
        pattern = re.compile(
            r"os\.path\.basename\((?:[^()]|\([^()]*\))*\)\s*(==|in)\s*"
            r"(\"assistant\.db\"|LEDGER_FILENAME_GUARD|REFUSED_STORES)")
        hits = []
        for path in sorted((TREE / "core").rglob("*.py")):
            if path.name == "store_base.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text):
                hits.append(str(path.relative_to(TREE)))
        self.assertEqual(hits, [])


# ---------------------------------------------------------------- M-V4

class UnifiedActivityTests(unittest.TestCase):
    def _fixture(self, tmp):
        chain_mod.configure(data_dir=tmp)
        rf_mod.configure(data_dir=tmp)
        rf_mod.log_activity_human("gaps27 human line")
        ledger = cost_mod.CostLedger(
            db_path=tmp / "cost_ledger.sqlite3")
        ledger.record_turn("cloud_text", "llama-3.1-8b-instant", 10, 5)
        ledger.close()
        (tmp / "activity.log").write_text(
            "2026-01-01T00:00:00 | get_current_time_date | success\n",
            encoding="utf-8")
        for name, ddl, ins in (
            ("translations",
             "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY"
             " AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,"
             " payload TEXT NOT NULL, prev_hash TEXT NOT NULL,"
             " hash TEXT NOT NULL)",
             "INSERT INTO events (ts, kind, payload, prev_hash, hash)"
             " VALUES (1700000000.0, 'translate', '{\"x\": 1}',"
             " '0', '0')"),
            ("invoice",
             "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY"
             " AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL,"
             " payload TEXT NOT NULL, prev_hash TEXT NOT NULL,"
             " hash TEXT NOT NULL)",
             "INSERT INTO events (ts, kind, payload, prev_hash, hash)"
             " VALUES (1700000001.0, 'render', '{\"y\": 2}',"
             " '0', '0')"),
        ):
            conn = sqlite3.connect(str(tmp / f"{name}.sqlite3"))
            conn.execute(ddl)
            conn.execute(ins)
            conn.commit()
            conn.close()

    def test_read_unified_covers_four_sources(self):
        from core.activity_feed import read_unified
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            try:
                self._fixture(tmp)
                entries = read_unified(limit=50, data_dir=tmp)
                sources = {e.get("source") for e in entries}
                self.assertTrue(
                    {"activity", "cost", "ledger", "log"} <= sources)
                self.assertEqual(entries[0]["source"], "cost")
                self.assertEqual(
                    read_unified(limit=50, data_dir=tmp,
                                 include=("cost",)),
                    [e for e in entries if e["source"] == "cost"])
                self.assertEqual(read_unified(limit=0, data_dir=tmp),
                                 [])
            finally:
                rf_mod.reset_configuration()
                chain_mod.reset_configuration()

    def test_read_unified_surfaces_degraded_states(self):
        # Field-check fix #2 (T7 #19/#20): a receipt store or
        # Live cost ledger that lost rows puts one marker per
        # degraded state in the feed (source "observability"),
        # so the failure is visible to the user instead of
        # reaching only the model as `observability: degraded`.
        from core import tool_receipts as receipts_mod
        from core.activity_feed import read_unified
        receipts_mod.reset_receipt_degraded()
        cost_mod.reset_live_record_degraded()
        self.addCleanup(receipts_mod.reset_receipt_degraded)
        self.addCleanup(cost_mod.reset_live_record_degraded)
        receipts_mod._note_failure("attempt",
                                   RuntimeError("store down"))
        cost_mod.note_live_record_failure(RuntimeError("ledger down"))
        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
            markers = [e for e in entries
                       if e.get("source") == "observability"]
            self.assertEqual(len(markers), 2)
            by_kind = {m["title"].split(":")[0]: m for m in markers}
            self.assertIn("Receipts degraded", by_kind)
            self.assertIn("Cost ledger degraded", by_kind)
            self.assertEqual(
                {m.get("kind") for m in markers}, {"degraded"})
            self.assertIn("store down",
                          by_kind["Receipts degraded"]["title"])
            self.assertIn("ledger down",
                          by_kind["Cost ledger degraded"]["title"])

    def test_read_unified_has_no_markers_when_healthy(self):
        # The markers appear only when a state is actually
        # degraded — a healthy system reads exactly as before.
        from core import tool_receipts as receipts_mod
        from core.activity_feed import read_unified
        receipts_mod.reset_receipt_degraded()
        cost_mod.reset_live_record_degraded()
        self.addCleanup(receipts_mod.reset_receipt_degraded)
        self.addCleanup(cost_mod.reset_live_record_degraded)
        with tempfile.TemporaryDirectory() as tmp:
            entries = read_unified(limit=50, data_dir=Path(tmp))
            self.assertEqual(
                [e for e in entries
                 if e.get("source") == "observability"], [])

    def test_cost_read_creates_no_store_file(self):
        # Parent fix (2026-11-14): the unified read must never
        # create a store file — CostLedger._connect() creates an
        # empty cost_ledger.sqlite3 when absent, so a missing
        # ledger answers empty before any CostLedger exists.
        from core.activity_feed import read_unified
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self.assertEqual(
                read_unified(limit=50, data_dir=tmp,
                             include=("cost",)), [])
            self.assertFalse((tmp / "cost_ledger.sqlite3").exists())

    def test_default_feed_stays_chain_only(self):
        import importlib
        va_chain = importlib.import_module(
            "voice_assistant.core.audit_chain")
        va_rf = importlib.import_module(
            "voice_assistant.core.record_family")
        import gui.window_activity as wa
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            va_chain.configure(data_dir=tmp)
            try:
                va_rf.configure(data_dir=tmp)
                try:
                    va_rf.log_activity_human("gaps27 human line")
                    va_chain.append("audit",
                                    {"tool": "gaps27_probe_tool"})
                    feed = wa.activity_feed(50)
                    self.assertTrue(feed.get("available"))
                    self.assertEqual(
                        [e.get("title") for e in
                         feed.get("entries", [])],
                        ["gaps27 human line"])
                    unified = wa.unified_feed(50)
                    self.assertTrue(unified.get("available"))
                    self.assertTrue(any(
                        e.get("source") == "activity"
                        for e in unified.get("entries", [])))
                finally:
                    va_rf.reset_configuration()
            finally:
                va_chain.reset_configuration()


# ---------------------------------------------------------------- M-V5

class StepSequenceTests(unittest.TestCase):
    def _fixture_dir(self, tmp):
        (tmp / "routines.json").write_text(json.dumps([{
            "name": "gaps27_routine",
            "actions": [{"tool": "get_current_time_date",
                         "args": {}}]}]), encoding="utf-8")
        (tmp / "task_ledger.json").write_text(json.dumps({
            "version": 1,
            "tasks": [{"id": "t1", "name": "gaps27 plan",
                       "steps": [{"tool": "get_current_time_date",
                                  "args": {}}]}]}), encoding="utf-8")
        wf_dir = tmp / "workflows"
        wf_dir.mkdir()
        (wf_dir / "w1.json").write_text(json.dumps({
            "workflow_id": "w1",
            "tasks": [{"task_id": "t1",
                       "tool": "get_current_time_date",
                       "args": {}}]}), encoding="utf-8")
        return wf_dir

    def test_listing_covers_five_kinds(self):
        from core import step_sequence as seq
        from core.anticipation_routines import Routine as AntRoutine
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self._fixture_dir(tmp)
            ant = AntRoutine(name="gaps27_ant", trigger="morning",
                             actions=["check the calendar"])
            replay = {"steps": [{"tool": "get_current_time_date",
                                 "args": {}}]}
            seqs = seq.list_sequences(
                data_dir=tmp, anticipation=[ant], replay=[replay])
            self.assertEqual({s.kind for s in seqs},
                             {"routine", "anticipation", "staged",
                              "replay", "dag"})
            self.assertEqual(
                {s.steps[0].tool for s in seqs
                 if s.steps and s.steps[0].tool},
                {"get_current_time_date"})
            ant_seq = next(s for s in seqs
                           if s.kind == "anticipation")
            self.assertEqual(ant_seq.steps[0].tool, "")
            self.assertEqual(ant_seq.steps[0].text,
                             "check the calendar")
            dag_seq = next(s for s in seqs if s.kind == "dag")
            self.assertEqual(dag_seq.steps[0].label, "t1")

    def test_damaged_records_excluded_never_raises(self):
        from core import step_sequence as seq
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wf_dir = self._fixture_dir(tmp)
            (tmp / "routines.json").write_text(
                '[{"name": "ok", "actions": [{"tool": "x", "args": {}}]},'
                ' {"name": 3, "actions": []}]', encoding="utf-8")
            (tmp / "task_ledger.json").write_text("not json",
                                                  encoding="utf-8")
            (wf_dir / "bad.json").write_text(
                '{"workflow_id": "bad", "tasks": [{"task_id": "x"}]}',
                encoding="utf-8")
            seqs = seq.list_sequences(data_dir=tmp)
            self.assertEqual([s.id for s in seqs],
                             ["routine:ok", "dag:w1"])
            self.assertEqual(seq.list_sequences(
                data_dir=tmp / "nope"), [])


# ---------------------------------------------------------------- M-V6

class MemoryReadTests(unittest.TestCase):
    def test_source_migration_and_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(str(legacy))
            conn.execute(
                "CREATE TABLE facts (id INTEGER PRIMARY KEY"
                " AUTOINCREMENT, category TEXT NOT NULL"
                " DEFAULT 'general', key TEXT NOT NULL,"
                " value TEXT NOT NULL,"
                " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                " updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
                " UNIQUE(category, key))")
            conn.execute(
                "INSERT INTO facts (category, key, value) VALUES"
                " ('general', 'legacy_fact', 'legacy_value')")
            conn.commit()
            conn.close()
            store = MemoryStore(db_path=legacy)
            self.assertTrue(store.save_fact(
                "general", "new_fact", "new_value",
                source="correction"))
            mem = read_memory_fn(db_path=legacy)
            by_key = {f["key"]: f for f in mem["facts"]}
            self.assertEqual(by_key["legacy_fact"]["source"], "unknown")
            self.assertEqual(by_key["new_fact"]["source"], "correction")
            self.assertEqual(by_key["new_fact"]["source_label"],
                             "You corrected me")
            # An unknown source value stores as "unknown".
            store.save_fact("general", "odd", "v", source="bogus")
            by_key = {f["key"]: f for f in
                      read_memory_fn(db_path=legacy)["facts"]}
            self.assertEqual(by_key["odd"]["source"], "unknown")

    def test_credential_hidden_and_per_item_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.db"
            store = MemoryStore(db_path=db)
            store.save_fact("general", "wifi_password", "hunter2")
            store.save_fact("general", "favourite_food", "dosa")
            mem = read_memory_fn(db_path=db)
            self.assertNotIn("hunter2", json.dumps(mem))
            self.assertNotIn("wifi_password", json.dumps(mem))
            self.assertEqual(mem["excluded"], 1)
            food = next(f for f in mem["facts"]
                        if f["key"] == "favourite_food")
            self.assertTrue(store.delete_fact(food["id"]))
            self.assertTrue(all(
                f["key"] != "favourite_food"
                for f in read_memory_fn(db_path=db)["facts"]))

    def test_episodes_ride_the_same_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE episodes (id INTEGER PRIMARY KEY"
                " AUTOINCREMENT, date TEXT NOT NULL, ts TEXT NOT NULL,"
                " kind TEXT NOT NULL, text TEXT NOT NULL)")
            conn.execute(
                "INSERT INTO episodes (date, ts, kind, text) VALUES"
                " ('2026-11-14', '2026-11-14T10:00:00', 'note',"
                " 'gaps27 episode')")
            conn.commit()
            conn.close()
            mem = read_memory_fn(db_path=db)
            self.assertEqual(len(mem["episodes"]), 1)
            self.assertEqual(mem["episodes"][0]["text"],
                             "gaps27 episode")

    def test_shared_missing_mixin(self):
        from core.translations import _TranslationsStore
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translations.sqlite3"
            store = _TranslationsStore(str(path))
            self.assertTrue(store._missing())
            self.assertFalse(path.exists())
            self.assertEqual(store.list_events(), [])
            self.assertFalse(path.exists())
        # No store module keeps its own _missing any more.
        for name in ("technical_analysis", "time_tracker",
                     "audio_localise", "optimise", "erp", "media_chat",
                     "invoice", "translations", "filament", "deals",
                     "vehicle_routes"):
            src = (TREE / "core" / f"{name}.py").read_text(
                encoding="utf-8")
            self.assertNotIn("def _missing", src, name)


# ---------------------------------------------------------------- M-V7

class BackupManifestTests(unittest.TestCase):
    def _fixture_data(self, tmp):
        data = Path(tmp) / "data"
        (data / "memories").mkdir(parents=True)
        (data / "memories" / "note.md").write_text(
            "# note\n", encoding="utf-8")
        (data / "milestones.jsonl").write_text(
            '{"event": "gaps27_fixture"}\n', encoding="utf-8")
        store = MemoryStore(db_path=data / "assistant.db")
        store.save_fact("general", "gaps27_fact", "gaps27_value")
        return data

    def test_backup_includes_assistant_db_and_restores_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = self._fixture_data(tmp)
            out = tmp / "bundle.zip"
            report = backup_mod.create_backup(str(data), {"k": "v"},
                                              str(out))
            check = backup_mod.verify_bundle(str(out))
            self.assertTrue(check.get("ok"))
            self.assertIn("assistant.db",
                          (check.get("manifest") or {}).get("hashes",
                                                           {}))
            self.assertIsNotNone(report.get("assistant_db"))
            target = tmp / "restored"
            target.mkdir()
            prev = MemoryStore(db_path=target / "assistant.db")
            prev.save_fact("general", "pre_existing", "keep_me")
            report2 = backup_mod.restore_bundle(str(out), str(target))
            restored = target / "assistant.db"
            kept = list(target.glob("assistant.db.pre-restore-*"))
            self.assertTrue(restored.exists())
            self.assertEqual(
                MemoryStore(db_path=restored).get_fact(
                    "general", "gaps27_fact")["value"], "gaps27_value")
            self.assertEqual(len(kept), 1)
            self.assertEqual(
                MemoryStore(db_path=kept[0]).get_fact(
                    "general", "pre_existing")["value"], "keep_me")
            self.assertIn("assistant.db", report2.get("applied", []))

    def test_absent_assistant_db_is_reported_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data = tmp / "data"
            (data / "memories").mkdir(parents=True)
            out = tmp / "bundle.zip"
            report = backup_mod.create_backup(str(data), {}, str(out))
            self.assertIsNone(report.get("assistant_db"))
            self.assertEqual(report.get("assistant_db_error"), "absent")
            self.assertTrue(backup_mod.verify_bundle(str(out)).get("ok"))


# ---------------------------------------------------------------- M-V8

class TelemetryOptInTests(unittest.TestCase):
    def test_setting_exists_default_off(self):
        import core.config_spec as cs
        from core.settings_schema import FIELD_SPECS, NovaSettingsData
        row = next((e for e in cs.QSETTINGS_FIELDS
                    if e[0] == "telemetry_enabled"), None)
        self.assertIsNotNone(row)
        self.assertEqual(row[1], "telemetry/enabled")
        self.assertIs(row[2], bool)
        self.assertIs(row[3], False)
        spec = FIELD_SPECS["telemetry_enabled"]
        self.assertIs(spec[1], False)
        self.assertEqual(spec[2], "bool")
        self.assertEqual(spec[3], "telemetry/enabled")
        self.assertIs(NovaSettingsData().telemetry_enabled, False)
        # Chat UI build (2026-11-16): +2 chat keys
        # (chat/composer_draft, chat/last_seen_id).
        # Field-check fix #7 (U4 #31): +1
        # (chat/last_seen_store_id). Field-check fix #12
        # (M11a): +2 (audio/output_device[_name]).
        # Rethink Build 5 (§9.1): +1 (browser/connect_offer_shown).
        self.assertEqual(len(cs.QSETTINGS_FIELDS), 96)

    def test_apply_settings_off_on_and_no_args(self):
        class _Stub:
            def __init__(self, enabled):
                self._enabled = enabled

            def value(self, key, default=None):
                if key == "telemetry/enabled":
                    return self._enabled
                return default

        sdk = telemetry_mod._load_otel() is not None
        try:
            telemetry_mod.reset_configuration()
            self.assertIs(
                telemetry_mod.apply_settings(_Stub(False)), False)
            self.assertIs(telemetry_mod.is_enabled(), False)
            telemetry_mod.reset_configuration()
            self.assertIs(
                telemetry_mod.apply_settings(_Stub(True)), sdk)
            self.assertIs(telemetry_mod.is_enabled(), sdk)
            telemetry_mod.reset_configuration()
            result = telemetry_mod.apply_settings()
            self.assertIsInstance(result, bool)
            self.assertIs(telemetry_mod.is_enabled(), False)
        finally:
            telemetry_mod.reset_configuration()


# ---------------------------------------------------------------- M-V9

class CostGuiTests(unittest.TestCase):
    def test_cost_summary_and_recent_turns(self):
        import gui.window_cost as wc
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "cost_ledger.sqlite3"
            ledger = cost_mod.CostLedger(db_path=db)
            ledger.record_turn("cloud_text", "llama-3.1-8b-instant",
                               10, 5)
            ledger.record_turn("live", "gemini-live", 0, 0)
            ledger.close()
            view = wc.cost_summary(db_path=db)
            self.assertTrue(view.get("available"))
            self.assertIn("spend_today_usd", view.get("summary", {}))
            turns = view.get("recent_turns", [])
            self.assertEqual(len(turns), 2)
            self.assertEqual({t.get("model") for t in turns},
                             {"llama-3.1-8b-instant", "gemini-live"})
            self.assertIn("llama-3.1-8b-instant",
                          wc.render_cost_text(view))

    def test_cost_and_memory_surfaced_in_settings(self):
        adv = (TREE / "gui" / "window_settings_advcards.py").read_text(
            encoding="utf-8")
        self.assertIn("window_cost", adv)
        self.assertIn("window_memory_panel", adv)
        self.assertIn("_open_cost_ledger", adv)
        self.assertIn("_open_memory_panel", adv)
        self.assertIn("_open_unified_activity", adv)

    def test_memory_panel_pure_functions(self):
        import gui.window_memory_panel as wmp
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mem.db"
            store = MemoryStore(db_path=db)
            store.save_fact("general", "favourite_food", "dosa")
            summary = wmp.memory_summary(db_path=db)
            self.assertTrue(summary.get("available"))
            self.assertEqual(len(summary.get("facts", [])), 1)
            self.assertEqual(
                summary["facts"][0]["source_label"], "You told me")
            self.assertIn(
                "favourite_food: dosa — You told me",
                wmp.render_memory_text(summary))
            fact_id = summary["facts"][0]["id"]
            self.assertTrue(
                wmp.delete_memory_fact(fact_id, db_path=db)["ok"])
            self.assertEqual(
                wmp.memory_summary(db_path=db)["facts"], [])


# ---------------------------------------------------------------- census

class CensusTests(unittest.TestCase):
    def test_census_unchanged(self):
        """No new tool: AUDIT-TOTALS and the live registry agree
        on 755/382/373/164, params 2077, grouped 752, closed 754."""
        totals = re.search(
            r'AUDIT-TOTALS: \{"total": (\d+), "read": (\d+), '
            r'"effectful": (\d+), "confirm_gated": (\d+), '
            r'"seam_wired": (\d+), "already_verified_in_handler": (\d+), '
            r'"honestly_unverifiable": (\d+), "next_pass_pool": (\d+)\}',
            (TREE / "TOOLS_LOOPHOLES_AUDIT.md").read_text(
                encoding="utf-8"))
        self.assertIsNotNone(totals)
        self.assertEqual(tuple(int(g) for g in totals.groups()),
                         (758, 383, 375, 166, 44, 309, 22, 0))  # INTENTIONAL — field-check fix #32 (2026-11-18): 755/373/307 -> 756/374/308 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756/374/164/308 -> 757/375/165/309 — one new tool run_plan; fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling; round-3 fix Q6 (2026-11-19): 757 -> 758, 382 -> 383 — one new tool get_morning_briefing
        from core.funnel.tools_registry import tools as _TOOLS
        from core.funnel.tools_gate import _CONFIRM_GATE_TOOLS
        import core.catalogue_groups_data as _cat
        tools = _TOOLS._tools
        grouped = set()
        for members in _cat.GROUP_MEMBERS.values():
            grouped.update(members)
        params = sum(len((s.get("parameters") or {}).get("properties")
                         or {}) for s in tools.values())
        self.assertEqual(len(tools), 758)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing
        self.assertEqual(params, 2079)  #Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 2078 -> 2079 — one new tool run_plan
        self.assertEqual(len([n for n in tools
                              if n in _CONFIRM_GATE_TOOLS]), 166)  # INTENTIONAL — round-3 fix #4 (2026-11-19): 164 -> 165 — one new tool run_plan (confirm-gated); fix #32 follow-up (2026-11-19): 165 -> 166 — run_anticipation_routine classified confirm-gated per parent ruling
        self.assertEqual(len(grouped), 755)  # INTENTIONAL — field-check fix #32 (2026-11-18): 752 -> 753 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 753 -> 754 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 754 -> 755 — one new tool get_morning_briefing
        self.assertEqual(len(tools) - 1, 757)  # closed = total - 1; field-check fix #32 (2026-11-18): 754 -> 755 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 755 -> 756 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 756 -> 757 — one new tool get_morning_briefing


if __name__ == "__main__":
    unittest.main()
