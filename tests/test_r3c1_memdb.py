"""Round-3 section C fix queue item 1 (C3-F2): a corrupt memory DB
must not kill the app at import.

Before the fix, module-level ``memory_store = MemoryStore()`` ran
_init_db at import; a corrupt data/assistant.db raised uncaught
``sqlite3.DatabaseError: file is not a database``, so the funnel
could not be imported and main.py exited with a traceback. After
the fix, construction quarantines the corrupt file (original bytes
kept, never deleted), starts a fresh store on the same path, and
reports the corrupt state with a plain-words notice and a repair
path. Healthy and fresh databases behave exactly as before.
"""
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.memory.memory import MemoryStore

try:
    # New in this fix; absent pre-fix, so the import is guarded
    # and the suite still collects (and fails first) there.
    from core.memory.memory import MemoryStoreUnavailableError
except ImportError:  # pragma: no cover — pre-fix only
    MemoryStoreUnavailableError = RuntimeError

GARBAGE = b"THIS-IS-NOT-A-SQLITE-DATABASE" * 64
TREE_ROOT = Path(__file__).resolve().parent.parent


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_garbage(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(GARBAGE)


def _build_valid_db(path: Path) -> MemoryStore:
    """A small healthy DB: 2 facts, 1 episode, 1 log row."""
    store = MemoryStore(path)
    assert store.save_fact("general", "home_city", "Mumbai")
    assert store.save_fact("general", "standup", "9am")
    store._execute(
        "INSERT INTO episodes (date, ts, kind, text) "
        "VALUES (?, ?, ?, ?)",
        ("2026-11-20", "2026-11-20T09:00:00", "note",
         "C3-F2 repair probe"))
    store._execute(
        "INSERT INTO conversation_logs (role, content, timestamp) "
        "VALUES (?, ?, ?)",
        ("user", "C3-F2 repair probe", "2026-11-20T09:00:00"))
    return store


def _import_probe(module: str, db_path: Path) -> subprocess.CompletedProcess:
    """Import `module` in a fresh interpreter with core.config.DB_PATH
    pointed at `db_path` BEFORE the memory module is imported, so
    the module-level store constructs against the staged file.
    Prints IMPORTED + the corrupt flags on success."""
    code = (
        "import core.config as _cfg\n"
        f"_cfg.DB_PATH = {str(db_path)!r}\n"
        f"import {module} as _m\n"
        "print('IMPORTED')\n"
        "import core.memory.memory as _mem\n"
        "print('FLAGS', getattr(_mem.memory_store,"
        " 'corrupt_db_detected', False),"
        " getattr(_mem.memory_store, 'quarantine_path', None))\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = ".pkgroot:."
    return subprocess.run(
        [sys.executable, "-c", code], cwd=str(TREE_ROOT), env=env,
        capture_output=True, text=True, timeout=180)


class TestCorruptConstruction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r3c1_memdb_"))

    def _corrupt_path(self) -> Path:
        path = self.tmp / "assistant.db"
        _write_garbage(path)
        return path

    def test_construction_succeeds_on_corrupt_db(self):
        # Fail-first: pre-fix this raises sqlite3.DatabaseError.
        store = MemoryStore(self._corrupt_path())
        self.assertIsNotNone(store)

    def test_store_reports_corrupt_not_empty(self):
        store = MemoryStore(self._corrupt_path())
        self.assertTrue(store.corrupt_db_detected)
        self.assertIn("not a database", store.corruption_error)
        self.assertIsNotNone(store.quarantine_path)
        # The fresh store is empty, but the corrupt state is
        # reported — never a silent empty store.
        self.assertEqual(store.get_all_facts(), [])

    def test_quarantine_holds_original_bytes(self):
        path = self._corrupt_path()
        store = MemoryStore(path)
        quarantine = Path(store.quarantine_path)
        self.assertTrue(quarantine.exists())
        self.assertEqual(quarantine.read_bytes(), GARBAGE)
        self.assertNotEqual(quarantine, path)

    def test_fresh_store_works_after_quarantine(self):
        store = MemoryStore(self._corrupt_path())
        self.assertTrue(store.save_fact("general", "home_city",
                                        "Mumbai"))
        got = store.get_fact("general", "home_city")
        self.assertEqual((got or {}).get("value"), "Mumbai")

    def test_notice_names_file_and_quarantine(self):
        store = MemoryStore(self._corrupt_path())
        notice = store.corruption_notice()
        self.assertIn("assistant.db", notice)
        self.assertIn(Path(store.quarantine_path).name, notice)
        self.assertIn("nothing was deleted", notice.lower())

    def test_repair_on_garbage_is_honest(self):
        store = MemoryStore(self._corrupt_path())
        result = store.repair_from_quarantine()
        self.assertFalse(result["readable"])
        self.assertEqual(result["facts_recovered"], 0)
        self.assertEqual(result["episodes_recovered"], 0)
        self.assertEqual(result["logs_recovered"], 0)
        self.assertIn("could not be read",
                      store.repair_notice(result))

    def test_declare_lost_records_choice(self):
        store = MemoryStore(self._corrupt_path())
        self.assertTrue(store.declare_quarantine_lost())
        marker = Path(store.quarantine_path + ".LOST")
        self.assertTrue(marker.exists())
        self.assertIn("declared lost", marker.read_text())
        # The bytes are still there — the declaration is a choice,
        # not a deletion.
        self.assertEqual(Path(store.quarantine_path).read_bytes(),
                         GARBAGE)

    def test_quarantine_failure_is_unusable_not_fatal(self):
        path = self._corrupt_path()
        with mock.patch.object(Path, "rename",
                               side_effect=OSError("no")):
            store = MemoryStore(path)
        self.assertTrue(store.corrupt_db_detected)
        self.assertTrue(store._store_unusable)
        self.assertIsNone(store.quarantine_path)
        with self.assertRaises(MemoryStoreUnavailableError) as ctx:
            store.get_all_facts()
        self.assertIn("assistant.db", str(ctx.exception))
        self.assertIn("could not be moved aside",
                      store.corruption_notice())


class TestRepairRecovers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r3c1_memdb_"))

    def _store_with_staged_quarantine(self, quarantine_bytes: bytes):
        """Corrupt construction (creates the quarantine), then stage
        `quarantine_bytes` as the quarantined file's contents."""
        path = self.tmp / "assistant.db"
        _write_garbage(path)
        store = MemoryStore(path)
        Path(store.quarantine_path).write_bytes(quarantine_bytes)
        return store

    def _valid_db_bytes(self) -> bytes:
        src = self.tmp / "source.db"
        _build_valid_db(src)
        return src.read_bytes()

    def test_repair_recovers_facts_from_readable_quarantine(self):
        store = self._store_with_staged_quarantine(
            self._valid_db_bytes())
        result = store.repair_from_quarantine()
        self.assertTrue(result["readable"])
        self.assertEqual(result["facts_recovered"], 2)
        self.assertEqual(result["episodes_recovered"], 1)
        self.assertEqual(result["logs_recovered"], 1)
        self.assertEqual(
            (store.get_fact("general", "home_city") or {}).get("value"),
            "Mumbai")

    def test_repair_is_idempotent(self):
        store = self._store_with_staged_quarantine(
            self._valid_db_bytes())
        first = store.repair_from_quarantine()
        second = store.repair_from_quarantine()
        self.assertEqual(first, second)
        self.assertEqual(len(store.get_all_facts()), 2)

    def test_repair_partial_damage_recovers_facts(self):
        # Episodes table dropped: that table's read fails, but the
        # facts still come back and the result stays readable.
        src = self.tmp / "source.db"
        _build_valid_db(src)
        conn = sqlite3.connect(str(src))
        conn.execute("DROP TABLE episodes")
        conn.commit()
        conn.close()
        store = self._store_with_staged_quarantine(src.read_bytes())
        result = store.repair_from_quarantine()
        self.assertTrue(result["readable"])
        self.assertEqual(result["facts_recovered"], 2)
        self.assertEqual(result["episodes_recovered"], 0)
        self.assertEqual(result["logs_recovered"], 1)

    def test_repair_with_no_quarantine(self):
        store = MemoryStore(self.tmp / "fresh.db")
        result = store.repair_from_quarantine()
        self.assertTrue(result["nothing_to_repair"])
        self.assertFalse(store.declare_quarantine_lost())


class TestHealthyAndFresh(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r3c1_memdb_"))

    def test_healthy_db_untouched(self):
        path = self.tmp / "assistant.db"
        _build_valid_db(path)
        before = _sha(path)
        store = MemoryStore(path)
        # getattr defaults: the flags are new in this fix — a
        # healthy store reported no corruption before it too.
        self.assertFalse(getattr(store, "corrupt_db_detected", False))
        self.assertIsNone(getattr(store, "quarantine_path", None))
        self.assertEqual(
            (store.get_fact("general", "home_city") or {}).get("value"),
            "Mumbai")
        self.assertEqual(_sha(path), before)

    def test_fresh_db_creation(self):
        path = self.tmp / "new.db"
        self.assertFalse(path.exists())
        store = MemoryStore(path)
        self.assertFalse(getattr(store, "corrupt_db_detected", False))
        self.assertTrue(path.exists())
        self.assertTrue(store.save_fact("general", "k", "v"))

    def test_locked_or_unopenable_db_still_raises(self):
        # OperationalError is a SUBCLASS of DatabaseError, so the
        # guard must discriminate by message: an unopenable file
        # (here the parent path is a plain file, not a directory)
        # must propagate, never trigger a quarantine.
        blocker = self.tmp / "blocker"
        blocker.write_text("not a directory")
        path = blocker / "assistant.db"
        with self.assertRaises(sqlite3.OperationalError):
            MemoryStore(path)
        self.assertEqual(list(self.tmp.glob("*.corrupt-*")), [])


class TestModuleImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="r3c1_memdb_"))

    def _corrupt_path(self) -> Path:
        path = self.tmp / "assistant.db"
        _write_garbage(path)
        return path

    def test_module_import_succeeds_on_corrupt_db(self):
        # Fail-first: pre-fix the subprocess dies with
        # sqlite3.DatabaseError at import.
        proc = _import_probe("core.memory.memory",
                             self._corrupt_path())
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn("IMPORTED", proc.stdout)
        self.assertIn("FLAGS True", proc.stdout)

    def test_funnel_import_succeeds_on_corrupt_db(self):
        # Control first: the funnel must import on a healthy staged
        # DB in this interpreter, or the corrupt leg proves nothing.
        healthy = self.tmp / "healthy.db"
        _build_valid_db(healthy)
        control = _import_probe("core.funnel.tools_handlers", healthy)
        if control.returncode != 0:
            self.skipTest("funnel does not import in this interpreter: "
                          + control.stderr[-300:])
        proc = _import_probe("core.funnel.tools_handlers",
                             self._corrupt_path())
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn("IMPORTED", proc.stdout)
        self.assertIn("FLAGS True", proc.stdout)


if __name__ == "__main__":
    unittest.main()
