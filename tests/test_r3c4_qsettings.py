"""Round-3 section C fix queue item 4 (C3-F4).

`qsettings_set` called store.sync() but never checked
store.status(), while its own docstring promises "a write that
cannot persist must be loud". Under a read-only HOME, `set`
claimed success and `get` returned the unwritten value
in-process while Nova.conf on disk was unchanged — the
in-process value diverged from the persisted value, silently.
The fix: after sync, a status other than NoError is checked
against the file bytes (a fresh QSettings on the same path
shares the writer's in-process cache and would report the
unflushed value, so the read-back runs on a temp copy), and a
genuine failure raises RuntimeError naming the key and the
status, with the store rolled back to the value the key held
before the write.

Read-only HOME in this lab: the process runs as root, so
chmod 0555 does NOT block writes, chattr +i is refused by the
overlayfs, and a read-only bind remount fails with EPERM.
/sys IS mounted read-only and even root cannot create
/sys/NovaAI — so an explicit-path store at
/sys/NovaAI/Nova.conf is the root-proof read-only store.
"""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from core import config_spec as cs

try:
    from PySide6.QtCore import QSettings as _QSettings
    _HAVE_QT = True
except ImportError:
    _HAVE_QT = False

GARBAGE = b"\x00\x01\x02GARBAGE-NOT-JSON {{{{\nno equals here\n[unclosed"
HEALTHY_CONF = "sounds_enabled=false\ntheme=Cyan\n"
READONLY_CONF = "/sys/NovaAI/Nova.conf"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quarantines(directory: Path, prefix: str):
    return sorted(directory.glob(f"{prefix}.corrupt-*"))


class _FailingStore:
    """Dict-backed store whose sync never persists and whose
    status() reports AccessError — the no-Qt double for a
    read-only HOME. fileName() points at a real temp conf
    holding the OLD value, so the read-back has bytes to judge
    (on an interpreter without Qt the read-back import fails
    and the store is treated as not persisted — the same loud
    outcome)."""

    def __init__(self, conf: Path, values=None):
        self._conf = conf
        self.data = dict(values or {})
        self.status_name = "AccessError"

    def fileName(self):
        return str(self._conf)

    def status(self):
        class _Status:
            name = "AccessError"
        return _Status()

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, val):
        self.data[key] = val

    def contains(self, key):
        return key in self.data

    def remove(self, key):
        self.data.pop(key, None)

    def sync(self):
        pass


class _HealthyFakeStore(_FailingStore):
    """The same double, but the sync succeeds: status NoError,
    so no read-back runs and no Qt is needed."""

    def __init__(self, conf: Path, values=None):
        super().__init__(conf, values)
        self.status_name = "NoError"

    def status(self):
        class _Status:
            name = "NoError"
        return _Status()


@unittest.skipUnless(_HAVE_QT, "read-only QSettings legs need Qt")
class TestReadOnlyQt(unittest.TestCase):
    """Real Qt, explicit-path stores in temp dirs (no XDG —
    the QSettings path is cached per process, so XDG switching
    inside one test process is not honest staging)."""

    def _store(self, content=None):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3c4_")
        self.addCleanup(self._tmp.cleanup)
        conf = Path(self._tmp.name) / "Nova.conf"
        if content is not None:
            if isinstance(content, bytes):
                conf.write_bytes(content)
            else:
                conf.write_text(content, encoding="utf-8")
        return conf, _QSettings(str(conf), _QSettings.IniFormat)

    def tearDown(self):
        cs.reset_qsettings_corruption()
        super().tearDown()

    def test_readonly_set_raises_naming_key_and_status(self):
        store = _QSettings(READONLY_CONF, _QSettings.IniFormat)
        with self.assertRaises(RuntimeError) as caught:
            cs.qsettings_set("theme", "Solar", settings=store)
        message = str(caught.exception)
        self.assertIn("'theme'", message)
        self.assertIn("AccessError", message)

    def test_get_after_failed_set_returns_default(self):
        store = _QSettings(READONLY_CONF, _QSettings.IniFormat)
        with self.assertRaises(RuntimeError):
            cs.qsettings_set("theme", "Solar", settings=store)
        # The unwritten value must not present as persisted.
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Cyan")
        self.assertFalse(Path("/sys/NovaAI").exists())

    def test_readonly_remove_of_unset_key_is_vacuous(self):
        # Nothing is set and no file exists: there is no write
        # to persist, so the remove cannot fail — no raise, and
        # the key still reads as its default. (A remove of a
        # SET key that cannot persist raises; that is pinned by
        # the failing-fake-store leg below, where an old value
        # can actually be staged.)
        store = _QSettings(READONLY_CONF, _QSettings.IniFormat)
        cs.qsettings_remove("theme", settings=store)
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Cyan")

    def test_healthy_roundtrip_byte_identical(self):
        conf, store = self._store()
        cs.qsettings_set("theme", "Solar", settings=store)
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Solar")
        self.assertEqual(conf.read_bytes(),
                         b"[General]\ntheme=Solar\n")
        fresh = _QSettings(str(conf), _QSettings.IniFormat)
        self.assertEqual(fresh.value("theme"), "Solar")
        self.assertEqual(cs.qsettings_get("theme", settings=fresh),
                         "Solar")

    def test_stale_status_does_not_break_quarantine_write(self):
        # C3-F3 regression: after a corrupt read the store's
        # status stays FormatError even when the sync that
        # follows it succeeds — the write must still land.
        conf, store = self._store(GARBAGE)
        before = _sha(conf)
        cs.qsettings_set("sounds_enabled", False, settings=store)
        kept = _quarantines(Path(self._tmp.name), "Nova.conf")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]), before)
        self.assertFalse(cs.qsettings_get("sounds_enabled",
                                         settings=store))

    def test_stale_status_grouped_key_write(self):
        # Same stale-status shape, but with a grouped key
        # ("chat/composer_draft" lives in the [chat] section):
        # the temp-copy read-back must resolve the group, not
        # look for a top-level key.
        conf, store = self._store(GARBAGE)
        cs.qsettings_set("chat/composer_draft", "hello",
                         settings=store)
        self.assertEqual(cs.qsettings_get("chat/composer_draft",
                                          settings=store), "hello")
        kept = _quarantines(Path(self._tmp.name), "Nova.conf")
        self.assertEqual(len(kept), 1)

    def test_corrupt_still_discloses_before_set(self):
        conf, store = self._store(GARBAGE)
        record = cs.qsettings_corruption_state(settings=store)
        self.assertIsNotNone(record)
        self.assertIn("Nova.conf", record["path"])
        self.assertIn("not a valid settings file", record["error"])
        cs.qsettings_set("sounds_enabled", False, settings=store)
        self.assertFalse(cs.qsettings_get("sounds_enabled",
                                         settings=store))

    def test_missing_conf_is_healthy_empty(self):
        conf, store = self._store()
        self.assertTrue(cs.qsettings_get("sounds_enabled",
                                        settings=store))
        self.assertIsNone(cs.qsettings_corruption_state(
            settings=store))
        cs.qsettings_set("sounds_enabled", False, settings=store)
        self.assertTrue(conf.exists())
        self.assertEqual(_quarantines(Path(self._tmp.name),
                                      "Nova.conf"), [])


class TestFailingFakeStore(unittest.TestCase):
    """The rollback semantics, pinned without Qt: a failed set
    puts the OLD persisted value back, a failed remove puts
    the removed value back. Runs on both interpreters."""

    def _conf_with(self, text):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3c4_fake_")
        self.addCleanup(self._tmp.cleanup)
        conf = Path(self._tmp.name) / "Nova.conf"
        conf.write_text(text, encoding="utf-8")
        return conf

    def test_failed_set_raises_and_rolls_back_to_old_value(self):
        conf = self._conf_with("[General]\ntheme=Solar\n")
        store = _FailingStore(conf, {"theme": "Solar"})
        with self.assertRaises(RuntimeError) as caught:
            cs.qsettings_set("theme", "Amber", settings=store)
        message = str(caught.exception)
        self.assertIn("'theme'", message)
        self.assertIn("AccessError", message)
        # Not the new value, not the default — the old one.
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Solar")
        self.assertEqual(conf.read_text(encoding="utf-8"),
                         "[General]\ntheme=Solar\n")

    def test_failed_remove_raises_and_restores_value(self):
        conf = self._conf_with("[General]\ntheme=Solar\n")
        store = _FailingStore(conf, {"theme": "Solar"})
        with self.assertRaises(RuntimeError):
            cs.qsettings_remove("theme", settings=store)
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Solar")
        self.assertTrue(cs.qsettings_contains("theme",
                                              settings=store))

    def test_failed_set_of_unset_key_rolls_back_to_absent(self):
        conf = self._conf_with("[General]\ntheme=Cyan\n")
        store = _FailingStore(conf, {"theme": "Cyan"})
        with self.assertRaises(RuntimeError):
            cs.qsettings_set("sounds_enabled", False, settings=store)
        self.assertTrue(cs.qsettings_get("sounds_enabled",
                                        settings=store))
        self.assertFalse(cs.qsettings_contains("sounds_enabled",
                                               settings=store))

    def test_healthy_fake_store_roundtrip(self):
        conf = self._conf_with("[General]\ntheme=Cyan\n")
        store = _HealthyFakeStore(conf, {"theme": "Cyan"})
        cs.qsettings_set("theme", "Solar", settings=store)
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Solar")
        cs.qsettings_remove("theme", settings=store)
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Cyan")

    def test_statusless_fake_store_still_writes(self):
        # The old dict-backed doubles have no status() at all —
        # they must keep working exactly as before.
        from tests.test_qsettings import _FakeStore
        store = _FakeStore()
        cs.qsettings_set("theme", "Solar", settings=store)
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Solar")


if __name__ == "__main__":
    unittest.main()
