"""Loophole batch 6 — integrity theme (2026-11-22).

#9  signed update: the signed feed / hash-verified download /
    journaled apply already existed; what was missing was any
    way to UNDO an applied update or to throw away a staged one.
    rollback_available() reports the backed-up previous build,
    rollback_to_previous() requests the rollback (the restore
    runs at next startup inside apply_staged(), never under the
    running app), discard_staged_update() clears the staging
    dir, and the About card carries both buttons. No funnel
    tool, skill or voice path calls the updater.
#12 tampered-ledger reads: verify_chain_detail() always named
    the first break, but every read path read through it.
    read_trusted() returns the verified prefix only; the
    record-family reads, audit.read_recent and the wiring
    miner read through it; the Activity feed names the break
    instead of silently showing fewer entries.
#13 atomic migration: the DB->files migration could regress a
    newer file record with a stale DB value, raised instead of
    failing safe, and wrote a receipt with no proof the move
    completed. It now never regresses a newer file, never
    raises out of migrate_if_needed(), leaves an in-progress
    marker until a verification pass lands, and receipts only
    verified migrations.
#14 memory cap: the 100-fact cap counted the DB's three
    user-fact categories only, so file-only facts were
    remembered and prompted while invisible to it, and any
    non-user category bypassed it entirely. The cap now counts
    the DB+file union, the remember tool refuses non-user
    categories, and forget deletes both stores. Skill stores
    (save_fact directly) stay uncapped by design.
#15 capped receipts archive: the live receipts file was capped
    at 5,000 lines but the archive directory grew without
    bound. It is now capped at _MAX_ARCHIVE_BYTES total,
    oldest-first, with the rotation recorded so the Activity
    feed announces it.

Runs under both interpreters (sys + Qt). The About-card class
is Qt-only; everything else is stdlib. Every test runs against
temp dirs (NOVA_APP_ROOT / VYUK_MEMORIES_DIR / a configured
chain dir) — the live tree and live data/ are never touched.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import threading
import types as _types_mod
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent

import core.activity_feed as activity_feed  # noqa: E402
import core.audit as audit_mod  # noqa: E402
import core.audit_chain as ac  # noqa: E402
import core.record_family as rf  # noqa: E402
import core.tool_receipts_store as rstore_mod  # noqa: E402
from core.funnel import tools_handlers_command_url as thcu  # noqa: E402
from core.health import updater  # noqa: E402
from core.memory import learning as learn  # noqa: E402
from core.memory import memory_files as mf  # noqa: E402
from core.memory.memory import MemoryStore  # noqa: E402
from core.memory.memory import USER_FACT_CATEGORIES  # noqa: E402
from core.tool_receipts_store import ReceiptStore  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication as _QA  # noqa: E402
    from PySide6.QtWidgets import QVBoxLayout as _QVBoxLayout  # noqa: E402
    _QT = True
except Exception:  # noqa: BLE001 — sys interpreter has no Qt
    _QT = False

_APP = None
_VA_SNAPSHOT = {}
SettingsAdvCardsMixin = None
BrainServicesMixin = None


def setUpModule():
    """Synthesise the voice_assistant.* alias (the
    test_settings_redesign shape) and import the GUI mixins
    under Qt, so the About-card class can build the real card.
    The live data/ dir is never touched: every test uses temp
    dirs."""
    global _APP, _VA_SNAPSHOT, SettingsAdvCardsMixin, BrainServicesMixin
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    tree = str(ROOT)
    pkg = _types_mod.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = _types_mod.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    sys.modules["voice_assistant"] = pkg
    sys.modules["voice_assistant.core"] = core
    if not _QT:
        return
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    try:
        from gui.window_settings_advcards import (  # noqa: E402
            SettingsAdvCardsMixin as _SACM)
        SettingsAdvCardsMixin = _SACM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        SettingsAdvCardsMixin = None
    try:
        from gui.window_brain_services import (  # noqa: E402
            BrainServicesMixin as _BSM)
        BrainServicesMixin = _BSM
    except Exception:  # noqa: BLE001 — GUI import failed, skip
        BrainServicesMixin = None


def tearDownModule():
    """Restore the voice_assistant.* modules."""
    for key in [k for k in sys.modules
                if k == "voice_assistant"
                or k.startswith("voice_assistant.")]:
        if key not in _VA_SNAPSHOT:
            del sys.modules[key]
    for key, mod in _VA_SNAPSHOT.items():
        sys.modules[key] = mod


# ---------------------------------------------------------------- helpers
def _make_db(tmp, rows):
    """Temp MemoryStore with facts [(category, key, value,
    updated_at)]."""
    store = MemoryStore(str(tmp / "assistant.db"))
    for cat, key, val, ts in rows:
        store.save_fact(cat, key, val)
    conn = sqlite3.connect(str(tmp / "assistant.db"))
    try:
        for cat, key, val, ts in rows:
            conn.execute(
                "UPDATE facts SET updated_at = ? WHERE category = ? "
                "AND key = ?", (ts, cat, key))
        conn.commit()
    finally:
        conn.close()
    return store


def _file_record(category, key, value, updated_at, kind="fact"):
    return {
        "category": category, "key": key, "kind": kind,
        "source": "user", "provenance": "", "created_at": updated_at,
        "updated_at": updated_at, "valid_from": "", "valid_to": "",
        "recorded_at": updated_at, "supersedes": "", "volatility": "0.3",
        "due_date": "", "last_verified": "", "expires_at": "",
        "value": value,
    }


def _fill_user_facts(store, n=100):
    for i in range(n):
        store.save_fact("general", f"capkey{i:03d}", f"capval{i}")


def _seed_chain(tmp, n=5, kind="b6-seed"):
    ac.configure(data_dir=tmp)
    rf.configure(data_dir=tmp)
    ids = [ac.append(kind, {"n": i, "marker": "B6-SEED"})
           for i in range(n)]
    return ids


def _tamper_entry(tmp, index=2):
    """Rewrite entry `index` (0-based, oldest-first) with altered
    data, keeping its stored hash — a hash mismatch at that link."""
    path = ac.store_path()
    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[index])
    entry["data"] = {"n": 999, "marker": "B6-TAMPERED"}
    lines[index] = json.dumps(entry, sort_keys=True,
                              separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _reset_chain():
    ac.reset_configuration()
    rf.reset_configuration()


def _attempt_line(rid, i):
    return json.dumps({
        "type": "attempt", "receipt_id": rid, "tool": "write_file",
        "args_signature": f"sig-{i}", "effect_signatures": [f"sig-{i}"],
        "args_preview": json.dumps({"filepath": f"/tmp/b6_{i}.txt"}),
        "handle_id": None, "snapshot_stamp": None, "compensations": [],
        "manual_steps": [], "created_at": 1796000000.0 + i * 0.01,
        "status": "attempted", "dispatched": True,
        "outcome_summary": "",
    }, sort_keys=True)


class _Base(unittest.TestCase):
    """One temp dir per test; the NOVA_APP_ROOT /
    VYUK_MEMORIES_DIR env seams and the chain configuration are
    restored after every test, so no test leaks state into the
    next (and none ever points at the live tree or live data)."""

    def setUp(self):
        super().setUp()
        self._tmp = pathlib.Path(
            tempfile.mkdtemp(prefix="b6suite_"))
        self._saved_env = {k: os.environ.get(k) for k in (
            "NOVA_APP_ROOT", "VYUK_MEMORIES_DIR")}
        self._orig_cap = rstore_mod._MAX_ARCHIVE_BYTES

    def tearDown(self):
        for key, val in self._saved_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        rstore_mod._MAX_ARCHIVE_BYTES = self._orig_cap
        rstore_mod.reset_prune_archive_state()
        _reset_chain()
        shutil.rmtree(self._tmp, ignore_errors=True)
        super().tearDown()

    def _set_app_root(self, root):
        os.environ["NOVA_APP_ROOT"] = str(root)

    def _set_memories(self, base):
        os.environ["VYUK_MEMORIES_DIR"] = str(
            pathlib.Path(base) / "memories")

    def _fake_root(self, version="6.3.0"):
        """A minimal app root (version file + data dir) for the
        updater legs — the updater resolves its package root
        from NOVA_APP_ROOT, so no tree copy is needed."""
        root = self._tmp / "root"
        (root / "core").mkdir(parents=True, exist_ok=True)
        (root / "core" / "version.py").write_text(
            f'VERSION = "{version}"\n', encoding="utf-8")
        (root / "data").mkdir(parents=True, exist_ok=True)
        self._set_app_root(root)
        return root

    def _fake_backup(self, root, version="6.2.0"):
        backup = root / "data" / "update_backup" / version
        (backup / "core").mkdir(parents=True, exist_ok=True)
        (backup / "core" / "version.py").write_text(
            f'VERSION = "{version}"\n', encoding="utf-8")
        return backup


# ------------------------------------------------------------------- #9
class UpdaterTests(_Base):
    """#9 — rollback + discard seams on the signed updater."""

    def test_unsigned_feed_is_refused(self):
        """Control: an unsigned feed is refused (the verifier
        exists and runs before any parse). The app root points
        at a temp dir so the check's log line never lands in
        the live data/activity.log."""
        root = self._fake_root()
        manifest = json.dumps(
            {"version": "9.9.9", "url": "http://127.0.0.1/x.zip",
             "sha256": "0" * 64}).encode()
        blob = {"manifest": manifest}

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.endswith(".sig"):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(blob["manifest"])

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/feed.json"
            res = updater.check(feed_url=url)
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual(res.get("state"), "untrusted")

    def test_no_funnel_or_skill_handler_calls_the_updater(self):
        """Control: no funnel/skill handler calls the updater —
        the seams are Settings-only, never voice-triggered."""
        hits = []
        roots = [ROOT / "core" / "funnel"]
        roots += sorted((ROOT / "core").glob("skills*.py"))
        roots += sorted((ROOT / "core").glob("skills_*.py"))
        needles = ("updater.check(", "download_and_stage(",
                   "apply_staged(", "rollback_to_previous(",
                   "discard_staged_update(")
        for root in roots:
            paths = [root] if root.is_file() \
                else list(root.rglob("*.py"))
            for path in paths:
                for line in path.read_text(
                        encoding="utf-8", errors="replace").splitlines():
                    if line.strip().startswith("#"):
                        continue
                    for needle in needles:
                        if needle in line:
                            hits.append(f"{path.name}:{needle}")
        self.assertEqual(hits, [])

    def test_rollback_available_reports_the_backup(self):
        root = self._fake_root()
        self._fake_backup(root, "6.2.0")
        res = updater.rollback_available()
        self.assertTrue(res.get("available"))
        self.assertEqual(res.get("version"), "6.2.0")

    def test_rollback_not_available_without_a_backup(self):
        self._fake_root()
        res = updater.rollback_available()
        self.assertFalse(res.get("available"))
        self.assertIsNone(res.get("version"))

    def test_rollback_skips_a_backup_with_no_version_file(self):
        root = self._fake_root()
        backup = root / "data" / "update_backup" / "6.2.0"
        backup.mkdir(parents=True, exist_ok=True)
        res = updater.rollback_available()
        self.assertFalse(res.get("available"))

    def test_rollback_skips_a_backup_newer_than_running(self):
        root = self._fake_root(version="6.3.0")
        self._fake_backup(root, "6.4.0")
        res = updater.rollback_available()
        self.assertFalse(res.get("available"))

    def test_rollback_request_then_apply_restores_previous(self):
        root = self._fake_root(version="6.3.0")
        self._fake_backup(root, "6.2.0")
        (root / "data" / "update_applied.json").write_text(
            json.dumps({"from": "6.2.0", "to": "6.3.0"}),
            encoding="utf-8")
        req = updater.rollback_to_previous()
        self.assertEqual(req.get("state"), "rollback_pending")
        res = updater.apply_staged()
        self.assertEqual(res.get("state"), "rolled_back")
        live = (root / "core" / "version.py").read_text(
            encoding="utf-8")
        self.assertIn('"6.2.0"', live)

    def test_rollback_refused_when_an_update_is_staged(self):
        root = self._fake_root(version="6.3.0")
        self._fake_backup(root, "6.2.0")
        staging = root / "data" / "update_staging" / "6.4.0"
        staging.mkdir(parents=True, exist_ok=True)
        (root / "data" / "update_staging" / "staged.json").write_text(
            json.dumps({"version": "6.4.0", "files": {}}),
            encoding="utf-8")
        res = updater.rollback_to_previous()
        self.assertEqual(res.get("state"), "error")

    def test_rollback_refused_when_a_journal_is_pending(self):
        root = self._fake_root(version="6.3.0")
        self._fake_backup(root, "6.2.0")
        (root / "data" / "update_apply.json").write_text(
            json.dumps({"phase": "swapping", "version": "6.4.0",
                        "from_version": "6.3.0",
                        "backup_dir": "update_backup/6.3.0",
                        "inventory": []}),
            encoding="utf-8")
        res = updater.rollback_to_previous()
        self.assertEqual(res.get("state"), "error")

    def test_discard_staged_update_clears_staging(self):
        root = self._fake_root()
        staging = root / "data" / "update_staging" / "6.4.0"
        staging.mkdir(parents=True, exist_ok=True)
        (root / "data" / "update_staging" / "staged.json").write_text(
            json.dumps({"version": "6.4.0", "files": {}}),
            encoding="utf-8")
        res = updater.discard_staged_update()
        self.assertEqual(res.get("state"), "discarded")
        self.assertFalse(staging.exists())
        self.assertIsNone(updater.pending_update())

    def test_discard_when_nothing_staged_is_none(self):
        self._fake_root()
        res = updater.discard_staged_update()
        self.assertEqual(res.get("state"), "none")
        self.assertIsNone(updater.pending_update())


# ------------------------------------------------------------------ #12
class ChainTests(_Base):
    """#12 — tampered-ledger reads stop at the first break."""

    def test_read_trusted_stops_at_the_break(self):
        ids = _seed_chain(self._tmp, 5)
        _tamper_entry(self._tmp, 2)
        res = ac.read_trusted(kind="b6-seed", limit=None)
        got = [e["id"] for e in res["entries"]]
        self.assertEqual(got, [ids[1], ids[0]])
        self.assertFalse(res.get("ok"))
        self.assertEqual(
            (res.get("first_break") or {}).get("reason"),
            "hash mismatch")

    def test_read_trusted_agrees_with_verify(self):
        """The read and the verifier share one walk, so a read
        can never disagree with verify about where the chain
        breaks."""
        _seed_chain(self._tmp, 5)
        _tamper_entry(self._tmp, 2)
        detail = ac.verify_chain_detail()
        trusted = ac.read_trusted(kind="b6-seed", limit=None)
        self.assertEqual(trusted.get("first_break"),
                         detail.get("first_break"))

    def test_record_family_reads_stop_at_the_break(self):
        _seed_chain(self._tmp, 5)
        _tamper_entry(self._tmp, 2)
        got = rf.read_events("b6-seed", limit=None)
        self.assertEqual(len(got), 2)
        detail = rf.read_events_detail("b6-seed", limit=None)
        self.assertFalse(detail.get("ok"))
        self.assertIsNotNone(detail.get("first_break"))

    def test_audit_read_recent_stops_at_the_break(self):
        tmp = self._tmp
        ac.configure(data_dir=tmp)
        rf.configure(data_dir=tmp)
        audit_mod.configure(path=str(tmp / "audit.jsonl"))
        try:
            for i in range(5):
                ac.append("audit", {"tool": f"b6-audit-{i}",
                                    "marker": "B6-AUDIT"})
            _tamper_entry(tmp, 2)
            recent = audit_mod.read_recent(limit=50)
        finally:
            audit_mod.reset_configuration()
        tools = {r.get("tool") for r in recent}
        post = {"b6-audit-2", "b6-audit-3", "b6-audit-4"}
        pre = {"b6-audit-0", "b6-audit-1"}
        self.assertFalse(tools & post)
        self.assertTrue(tools & pre)

    def test_intact_chain_reads_everything(self):
        """Control: a healthy chain reads all of itself."""
        ids = _seed_chain(self._tmp, 5)
        res = ac.read_trusted(kind="b6-seed", limit=None)
        self.assertEqual(len(res["entries"]), 5)
        self.assertTrue(res.get("ok"))
        self.assertIsNone(res.get("first_break"))
        self.assertEqual(
            [e["id"] for e in res["entries"]],
            [ids[4], ids[3], ids[2], ids[1], ids[0]])

    def test_mining_skips_post_break_failures(self):
        from core import w8_wiring_mining as mining
        tmp = self._tmp
        ac.configure(data_dir=tmp)
        rf.configure(data_dir=tmp)
        for i in range(3):
            ac.append("b6-seed", {"n": i, "marker": "B6-SEED"})
        ac.append("b6-seed", {"n": 3, "error": "POST-BREAK-BOOM"})
        ac.append("b6-seed", {"n": 4, "marker": "B6-SEED"})
        _tamper_entry(tmp, 2)
        mined = mining.live_failure_records(limit=200)
        texts = json.dumps(mined)
        self.assertNotIn("POST-BREAK-BOOM", texts)

    def test_activity_feed_names_the_break(self):
        tmp = self._tmp
        ac.configure(data_dir=tmp)
        rf.configure(data_dir=tmp)
        for i in range(5):
            ac.append("activity", {"text": f"b6-act-{i}"})
        _tamper_entry(tmp, 2)
        entries = activity_feed.read_unified(
            data_dir=tmp, include=("activity",))
        titles = [str(e.get("title", "")) for e in entries]
        self.assertTrue(any("broken" in t for t in titles),
                        f"no break marker in {titles}")
        self.assertFalse(any("b6-act-2" in t or "b6-act-3" in t
                             or "b6-act-4" in t for t in titles),
                         f"post-break activity shown: {titles}")
        self.assertTrue(any("b6-act-0" in t or "b6-act-1" in t
                            for t in titles),
                        f"pre-break activity missing: {titles}")

# ------------------------------------------------------------------ #13
class MigrationTests(_Base):
    """#13 — atomic DB->files migration."""

    def test_migration_never_regresses_a_newer_file(self):
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        _make_db(tmp, [("general", "k", "OLD-FROM-DB",
                        "2026-01-01 00:00:00")])
        mf.write_fact(_file_record("general", "k", "NEW-IN-FILE",
                                   "2026-06-01T00:00:00"), base=base)
        mf.migrate_from_db(str(tmp / "assistant.db"), base=base)
        got = mf.read_fact("general", "k", base=base)
        self.assertIsNotNone(got)
        self.assertEqual(got.get("value"), "NEW-IN-FILE")

    def test_migration_overwrites_an_older_file(self):
        """Control: the stale guard is one-directional — a DB row
        NEWER than its file record still migrates."""
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        _make_db(tmp, [("general", "k", "NEW-FROM-DB",
                        "2026-06-01 00:00:00")])
        mf.write_fact(_file_record("general", "k", "OLD-IN-FILE",
                                   "2026-01-01T00:00:00"), base=base)
        mf.migrate_from_db(str(tmp / "assistant.db"), base=base)
        got = mf.read_fact("general", "k", base=base)
        self.assertIsNotNone(got)
        self.assertEqual(got.get("value"), "NEW-FROM-DB")

    def test_migrate_if_needed_never_raises(self):
        # The memories seam points at a temp dir: migrate_from_db
        # creates its store root before opening the DB, and with
        # base=None and no env override that root is the live
        # data/memories — a test-isolation leak, not a product
        # path (production calls it with the live store on purpose).
        self._set_memories(self._tmp / "base")
        res = mf.migrate_if_needed(str(self._tmp))
        self.assertIsNone(res)

    def test_receipt_is_verified_and_no_marker_is_left(self):
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        _make_db(tmp, [("general", f"k{i}", f"v{i}",
                        "2026-01-01 00:00:00") for i in range(3)])
        receipt = mf.migrate_from_db(str(tmp / "assistant.db"),
                                     base=base)
        marker = pathlib.Path(
            mf.migration_receipt_path(base)).parent / getattr(
                mf, "MIGRATION_IN_PROGRESS",
                "migration_in_progress.json")
        self.assertTrue(bool(receipt.get("verified")))
        self.assertEqual(receipt.get("verified_count"), 3)
        self.assertFalse(marker.exists())

    def test_partial_migration_reruns_to_completion(self):
        """Control: a crashed run (marker, no receipt) re-runs
        idempotently to a verified receipt."""
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        _make_db(tmp, [("general", f"k{i}", f"v{i}",
                        "2026-01-01 00:00:00") for i in range(3)])
        mf.write_fact(_file_record("general", "k0", "v0",
                                   "2026-01-01T00:00:00"), base=base)
        marker = pathlib.Path(
            mf.migration_receipt_path(base)).parent / getattr(
                mf, "MIGRATION_IN_PROGRESS",
                "migration_in_progress.json")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"started_at": "2026-11-22T00:00:00Z"}),
            encoding="utf-8")
        mf.migrate_if_needed(str(tmp / "assistant.db"), base=base)
        receipt = mf.migration_receipt(base=base)
        self.assertIsNotNone(receipt)
        self.assertTrue(bool(receipt.get("verified")))
        for i in range(3):
            got = mf.read_fact("general", f"k{i}", base=base)
            self.assertIsNotNone(got)
            self.assertEqual(got.get("value"), f"v{i}")


# ------------------------------------------------------------------ #14
class MemoryCapTests(_Base):
    """#14 — the 100-fact cap covers all fact stores."""

    def _remember_handler(self, store):
        orig = thcu.memory_store
        thcu.memory_store = store
        self.addCleanup(setattr, thcu, "memory_store", orig)
        return object.__new__(thcu.ToolHandlersCommandUrlMixin)

    def test_tool_refuses_a_non_user_category(self):
        store = MemoryStore(str(self._tmp / "assistant.db"))
        handler = self._remember_handler(store)
        with self.assertRaises(RuntimeError):
            handler._tool_remember_fact("my note", "something",
                                        category="notes")
        self.assertIsNone(store.get_fact("notes", "my note"))

    def test_combined_cap_counts_file_only_facts(self):
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        store = MemoryStore(str(tmp / "assistant.db"))
        _fill_user_facts(store, 100)
        mf.write_fact(_file_record("general", "file-only-key", "x",
                                   "2026-01-01T00:00:00"), base=base)
        self.assertEqual(store.count_remembered_facts(), 101)

    def test_combined_cap_deduplicates_a_key_in_both_stores(self):
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        store = MemoryStore(str(tmp / "assistant.db"))
        store.save_fact("general", "same-key", "v")
        mf.write_fact(_file_record("general", "same-key", "v",
                                   "2026-01-01T00:00:00"), base=base)
        self.assertEqual(store.count_remembered_facts(), 1)

    def test_tool_refuses_at_the_union_cap(self):
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        store = MemoryStore(str(tmp / "assistant.db"))
        _fill_user_facts(store, 100)
        mf.write_fact(_file_record("general", "file-only-key", "x",
                                   "2026-01-01T00:00:00"), base=base)
        handler = self._remember_handler(store)
        with self.assertRaises(RuntimeError):
            handler._tool_remember_fact("one-more", "v")

    def test_forget_deletes_both_stores(self):
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        store = MemoryStore(str(tmp / "assistant.db"))
        orig = learn.memory_store
        learn.memory_store = store
        self.addCleanup(setattr, learn, "memory_store", orig)
        store.save_fact("learned", "forgetme", "forgetval",
                        source="learned")
        mf.migrate_from_db(str(tmp / "assistant.db"), base=base)
        rows = [r for r in learn._learned_rows()
                if r.get("key") == "forgetme"]
        self.assertTrue(rows)
        self.assertTrue(learn.forget_learned_fact(rows[0]["id"]))
        merged = mf.dual_read(store.get_all_facts(), base=base)
        still = [f for f in merged if f.get("key") == "forgetme"]
        self.assertEqual(still, [])

    def test_learn_path_refuses_at_the_union_cap(self):
        tmp = self._tmp
        base = tmp / "base"
        self._set_memories(base)
        store = MemoryStore(str(tmp / "assistant.db"))
        orig = learn.memory_store
        learn.memory_store = store
        self.addCleanup(setattr, learn, "memory_store", orig)
        _fill_user_facts(store, 100)
        mf.write_fact(_file_record("general", "file-only-key", "x",
                                   "2026-01-01T00:00:00"), base=base)
        stored = learn.learn_from_text(
            "my favourite colour is teal", source="learned")
        self.assertEqual(stored, [])
        # And nothing landed: the union count is unchanged.
        self.assertEqual(
            store.count_remembered_facts(USER_FACT_CATEGORIES), 101)

    def test_skill_category_save_is_still_uncapped(self):
        """Control: skill stores are app state, not user facts —
        save_fact stays uncapped at a full cap."""
        store = MemoryStore(str(self._tmp / "assistant.db"))
        _fill_user_facts(store, 100)
        self.assertTrue(
            store.save_fact("flashcards", "card1", "front :: back"))


# ------------------------------------------------------------------ #15
class ReceiptsArchiveTests(_Base):
    """#15 — the receipts archive itself is capped."""

    def _run_prune_rounds(self, path, rounds=3, lines=20,
                          max_lines=5):
        for round_no in range(rounds):
            with open(path, "a", encoding="utf-8") as handle:
                for i in range(lines):
                    handle.write(_attempt_line(
                        f"r{round_no}-{i:03d}", round_no * 100 + i)
                        + "\n")
            ReceiptStore(path, max_lines=max_lines)._ensure_loaded()

    def test_archive_total_cap_rotates_the_oldest(self):
        rstore_mod._MAX_ARCHIVE_BYTES = 9000
        path = self._tmp / "tool_receipts.jsonl"
        self._run_prune_rounds(path)
        arch = self._tmp / "tool_receipts_archive"
        files = sorted(arch.glob("*.jsonl")) if arch.is_dir() else []
        total = sum(p.stat().st_size for p in files)
        self.assertLessEqual(len(files), 2)
        self.assertLessEqual(total, 9000 + 8000)
        state = rstore_mod.prune_archive_state()
        self.assertTrue(any("rotat" in str(k).lower()
                            for k in state.keys()))

    def test_single_prune_still_archives(self):
        """Control: one prune still archives the dropped rows."""
        path = self._tmp / "tool_receipts.jsonl"
        with open(path, "w", encoding="utf-8") as handle:
            for i in range(20):
                handle.write(_attempt_line(f"s-{i:03d}", i) + "\n")
        ReceiptStore(path, max_lines=5)._ensure_loaded()
        arch = self._tmp / "tool_receipts_archive"
        files = list(arch.glob("*.jsonl")) if arch.is_dir() else []
        rows = 0
        for p in files:
            rows += len(p.read_text(encoding="utf-8").splitlines())
        self.assertEqual(rows, 15)

    def test_rotation_is_announced_in_the_feed(self):
        ac.configure(data_dir=self._tmp)
        rf.configure(data_dir=self._tmp)
        rstore_mod._MAX_ARCHIVE_BYTES = 9000
        path = self._tmp / "tool_receipts.jsonl"
        self._run_prune_rounds(path)
        entries = activity_feed.read_unified(
            data_dir=self._tmp, include=("activity",))
        titles = [str(e.get("title", "")) for e in entries]
        self.assertTrue(any("rotated" in t for t in titles),
                        f"no rotation marker in {titles}")


# ------------------------------------------------- #9: the About card
@unittest.skipUnless(_QT, "Qt-only: the Settings card needs PySide6")
class AboutCardTests(_Base):
    """The About card carries the rollback + discard buttons
    (Qt only). The harness combines the two mixins the real
    window combines, so a missing handler or a broken build
    fails here exactly as it would in Settings."""

    def setUp(self):
        super().setUp()
        self.assertIsNotNone(
            SettingsAdvCardsMixin, "adv-cards mixin did not import")
        self.assertIsNotNone(
            BrainServicesMixin, "brain-services mixin did not import")

        class _Harness(SettingsAdvCardsMixin, BrainServicesMixin):
            pass

        self.harness = _Harness()
        self.harness._settings_card = (
            lambda _layout, _t, _s=None: _QVBoxLayout())
        self.harness._brain_engine = lambda: "Gemini"
        self.harness._open_data_folder = lambda: None
        self.harness._show_recent_activity = lambda: None
        self.harness.signals = None
        # _build_adv_cards2 builds every advanced card, and the
        # other cards connect handlers that live on mixins the
        # real window combines but this harness does not carry
        # (models import/export, settings import/export, the
        # ledger/panel openers). Stub them to no-ops: this class
        # tests the About card's own buttons, not those cards.
        # The About card's own handlers (_on_updates_clicked,
        # _on_rollback_clicked, _on_discard_update_clicked,
        # _refresh_update_buttons) stay REAL — they are the
        # behaviour under test.
        for _name in (
                "_w10_export_data", "_w10_import_data",
                "_w10_refresh_models", "_w10_remove_model",
                "_export_settings", "_import_settings",
                "_open_activity_feed", "_open_cost_ledger",
                "_open_egress_ledger", "_open_memory_panel",
                "_open_report_problem", "_open_templates",
                "_open_today", "_open_trust_status",
                "_open_unified_activity", "_show_page"):
            setattr(self.harness, _name, lambda *a, **k: None)

    def _build_about(self, root):
        self._set_app_root(root)
        layout = _QVBoxLayout()
        self.harness._build_adv_cards2(layout)
        return self.harness

    def test_rollback_button_visible_when_a_backup_exists(self):
        root = self._fake_root(version="6.3.0")
        self._fake_backup(root, "6.2.0")
        h = self._build_about(root)
        self.assertFalse(h.btn_rollback.isHidden())
        self.assertIn("6.2.0", h.btn_rollback.text())
        self.assertTrue(h.btn_discard_update.isHidden())

    def test_rollback_button_hidden_without_a_backup(self):
        root = self._fake_root(version="6.3.0")
        h = self._build_about(root)
        self.assertTrue(h.btn_rollback.isHidden())
        self.assertTrue(h.btn_discard_update.isHidden())

    def test_discard_button_visible_when_an_update_is_staged(self):
        root = self._fake_root(version="6.3.0")
        staging = root / "data" / "update_staging" / "6.4.0"
        staging.mkdir(parents=True, exist_ok=True)
        (root / "data" / "update_staging" / "staged.json").write_text(
            json.dumps({"version": "6.4.0", "files": {}}),
            encoding="utf-8")
        h = self._build_about(root)
        self.assertFalse(h.btn_discard_update.isHidden())
        self.assertIn("downloaded", h.update_label.text())

    def test_discard_click_clears_staging_and_relabels(self):
        root = self._fake_root(version="6.3.0")
        staging = root / "data" / "update_staging" / "6.4.0"
        staging.mkdir(parents=True, exist_ok=True)
        (root / "data" / "update_staging" / "staged.json").write_text(
            json.dumps({"version": "6.4.0", "files": {}}),
            encoding="utf-8")
        h = self._build_about(root)
        h.btn_discard_update.click()
        self.assertFalse(staging.exists())
        self.assertIsNone(updater.pending_update())
        self.assertIn("discarded", h.update_label.text())
        self.assertTrue(h.btn_discard_update.isHidden())

    def test_rollback_click_requests_and_relabels(self):
        root = self._fake_root(version="6.3.0")
        self._fake_backup(root, "6.2.0")
        (root / "data" / "update_applied.json").write_text(
            json.dumps({"from": "6.2.0", "to": "6.3.0"}),
            encoding="utf-8")
        h = self._build_about(root)
        h.btn_rollback.click()
        self.assertIn("restart", h.update_label.text().lower())
        self.assertFalse(h.btn_rollback.isEnabled())
        self.assertTrue((root / "data" / "update_apply.json").is_file())


if __name__ == "__main__":
    unittest.main()
