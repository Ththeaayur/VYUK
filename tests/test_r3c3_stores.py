"""Round-3 section C fix queue item 3 (C3-F3) — corrupt routines /
grants / settings stores read as empty/default, silently.

A corrupt file in any of the three stores used to read as if it
were empty, with no disclosure anywhere, and the next write
silently destroyed the old data for good. The fix (same shape
across all three, the C3-F2 memory-DB precedent): disclose the
corrupt file (log line + sticky state + a note in the tool
results / a startup notice) and quarantine it before any re-write
(original bytes kept alongside, never deleted). A genuinely
MISSING file is still a healthy empty store; a healthy file is
byte-identical in behaviour.

All legs run against temp paths — the live stores are never
touched. Routines/grants legs run on both interpreters; the
settings QSettings legs need Qt (skipped on sys), while the
settings file-probe leg is pure Python and runs everywhere.
"""
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.brain import planner as _planner
from core.brain import planner_plans as _plans
from core.brain import planner_routines as _routines
from core.brain import planner_storage as _storage
from core import config_spec as _config_spec
from core import permission_grants as _grants

try:
    from PySide6.QtCore import QSettings as _QSettings
    _HAVE_QT = True
except ImportError:
    _QSettings = None
    _HAVE_QT = False

GARBAGE = b"\x00\x01\x02GARBAGE-NOT-JSON {{{{\nno equals here\n[unclosed"
VALID_ROUTINE = [{
    "name": "Morning", "created_at": "2026-11-20T00:00:00",
    "actions": [{"tool": "get_morning_briefing", "args": {}}],
}]
VALID_GRANTS = {
    "version": 1,
    "grants": {"microphone": {
        "permission_id": "microphone", "state": "granted",
        "source": "user", "updated_at": "2026-11-20T00:00:00+00:00",
        "scope_note": ""}},
    "changes": [],
}
HEALTHY_CONF = "sounds_enabled=false\ntheme=Cyan\n"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quarantines(directory: Path, prefix: str) -> list[Path]:
    return sorted(directory.glob(f"{prefix}.corrupt-*"))


class _TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r3c3_")
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ---------------------------------------------------------------- routines
class TestRoutinesCorrupt(_TempDirCase):
    def setUp(self):
        super().setUp()
        self.path = self.dir / "routines.json"
        self._patchers = [
            mock.patch.object(_storage, "_ROUTINES_PATH", self.path),
            mock.patch.object(_planner, "_ROUTINES_PATH", self.path),
            mock.patch.object(_routines, "_ROUTINES_PATH", self.path),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        _reset = getattr(_storage, "reset_corruption_state", None)
        if _reset is not None:
            _reset()
        super().tearDown()

    def test_corrupt_garbage_list_discloses(self):
        self.path.write_bytes(GARBAGE)
        out = _routines.list_routines()
        self.assertIn("routines.json", out)
        self.assertIn("damaged", out)
        self.assertIn("Nothing was deleted", out)
        self.assertNotIn("1 routine", out)

    def test_corrupt_truncated_list_discloses(self):
        raw = json.dumps(VALID_ROUTINE).encode()
        self.path.write_bytes(raw[: len(raw) // 2])
        out = _routines.list_routines()
        self.assertIn("routines.json", out)
        self.assertIn("damaged", out)

    def test_corrupt_save_quarantines_first(self):
        self.path.write_bytes(GARBAGE)
        before = _sha(self.path)
        out = _routines.save_routine(
            "Evening", [{"tool": "get_morning_briefing", "args": {}}])
        kept = _quarantines(self.dir, "routines.json")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]), before)
        self.assertIn("set aside", out)
        self.assertIn(kept[0].name, out)
        now = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual({r["name"] for r in now}, {"Evening"})

    def test_corrupt_delete_does_not_destroy(self):
        self.path.write_bytes(GARBAGE)
        before = _sha(self.path)
        out = _routines.delete_routine("Morning")
        self.assertIn("No routine named", out)
        self.assertIn("damaged", out)
        self.assertEqual(_sha(self.path), before)
        self.assertEqual(_quarantines(self.dir, "routines.json"), [])

    def test_healthy_list_and_save_untouched(self):
        self.path.write_text(json.dumps(VALID_ROUTINE), encoding="utf-8")
        out = _routines.list_routines()
        self.assertIn("Morning", out)
        self.assertNotIn("damaged", out)
        self.assertNotIn("corrupt", out)
        _routines.save_routine(
            "Evening", [{"tool": "get_morning_briefing", "args": {}}])
        self.assertEqual(_quarantines(self.dir, "routines.json"), [])
        now = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual({r["name"] for r in now},
                         {"Morning", "Evening"})

    def test_missing_is_healthy_empty(self):
        out = _routines.list_routines()
        self.assertIn("No routines saved yet", out)
        self.assertNotIn("damaged", out)
        self.assertNotIn("corrupt", out)
        _routines.save_routine(
            "Evening", [{"tool": "get_morning_briefing", "args": {}}])
        self.assertTrue(self.path.exists())
        self.assertEqual(_quarantines(self.dir, "routines.json"), [])

    def test_wrong_top_level_type_is_corrupt(self):
        self.path.write_text(json.dumps({"name": "Morning"}),
                             encoding="utf-8")
        out = _routines.list_routines()
        self.assertIn("damaged", out)
        self.assertIn("routines.json", out)

    def test_damaged_entries_still_counted_not_corrupt(self):
        mixed = [VALID_ROUTINE[0], "not-a-record"]
        self.path.write_text(json.dumps(mixed), encoding="utf-8")
        out = _routines.list_routines()
        self.assertIn("1 damaged record", out)
        self.assertNotIn("could not be read", out)
        self.assertIsNone(_storage.store_corruption(self.path))

    def test_corruption_state_api(self):
        self.path.write_bytes(GARBAGE)
        _routines.list_routines()
        record = _storage.store_corruption(self.path)
        self.assertIsNotNone(record)
        self.assertIn("routines.json", record["path"])
        self.assertIn("not valid JSON", record["error"])
        notice = _storage.corruption_notice(self.path)
        self.assertIn("routines.json", notice)
        self.assertIn("Nothing was deleted", notice)
        # Reset clears the record — but the file is still corrupt,
        # so the next probe re-detects it live (detection is not
        # cached-only).
        _reset = getattr(_storage, "reset_corruption_state", None)
        if _reset is not None:
            _reset()
        again = _storage.store_corruption(self.path)
        self.assertIsNotNone(again)
        self.assertEqual(again["path"], record["path"])


class TestPlansShareTheShape(_TempDirCase):
    def setUp(self):
        super().setUp()
        self.path = self.dir / "plans.json"
        self._patchers = [
            mock.patch.object(_storage, "_PLANS_PATH", self.path),
            mock.patch.object(_planner, "_PLANS_PATH", self.path),
            mock.patch.object(_plans, "_PLANS_PATH", self.path),
        ]
        for patcher in self._patchers:
            patcher.start()

    def tearDown(self):
        for patcher in self._patchers:
            patcher.stop()
        _reset = getattr(_storage, "reset_corruption_state", None)
        if _reset is not None:
            _reset()
        super().tearDown()

    def test_corrupt_plans_list_discloses(self):
        self.path.write_bytes(GARBAGE)
        out = _plans.list_plans()
        self.assertIn("plans.json", out)
        self.assertIn("damaged", out)

    def test_corrupt_plans_create_quarantines_first(self):
        self.path.write_bytes(GARBAGE)
        before = _sha(self.path)
        out = _plans.create_plan(
            "Get ready", [{"text": "Pack a bag"}])
        kept = _quarantines(self.dir, "plans.json")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]), before)
        self.assertIn("set aside", out)


# ------------------------------------------------------------------ grants
class TestGrantsCorrupt(_TempDirCase):
    def setUp(self):
        super().setUp()
        self.path = self.dir / "permission_grants.json"
        _grants.set_path_override(self.path)
        _reset = getattr(_grants, "reset_corruption_state", None)
        if _reset is not None:
            _reset()

    def tearDown(self):
        _grants.clear_path_override()
        _reset = getattr(_grants, "reset_corruption_state", None)
        if _reset is not None:
            _reset()
        super().tearDown()

    def test_corrupt_garbage_state_disclosed_fail_closed(self):
        self.path.write_bytes(GARBAGE)
        self.assertEqual(_grants.get_state("microphone"), "denied")
        self.assertEqual(_grants.get_state("camera"), "denied")
        self.assertEqual(_grants.get_state("files"), "unknown")
        record = _grants.corruption_state()
        self.assertIsNotNone(record)
        self.assertIn("permission_grants.json", record["path"])
        self.assertIn("not valid JSON", record["error"])
        self.assertIn("permission_grants.json",
                      _grants.corruption_notice())
        self.assertIn("safe defaults", _grants.corruption_notice())

    def test_corrupt_truncated_disclosed(self):
        raw = json.dumps(VALID_GRANTS).encode()
        self.path.write_bytes(raw[: len(raw) // 2])
        self.assertEqual(_grants.get_state("microphone"), "denied")
        self.assertIsNotNone(_grants.corruption_state())

    def test_corrupt_set_quarantines_first(self):
        self.path.write_bytes(GARBAGE)
        before = _sha(self.path)
        self.assertTrue(_grants.set_permission_state(
            "microphone", "granted", via="settings"))
        kept = _quarantines(self.dir, "permission_grants.json")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]), before)
        self.assertIsNone(_grants.corruption_state())
        now = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(now["grants"]["microphone"]["state"], "granted")

    def test_corrupt_take_back_refuses_without_destroying(self):
        self.path.write_bytes(GARBAGE)
        before = _sha(self.path)
        self.assertFalse(_grants.clear_permission_decision(
            "microphone", via="settings"))
        self.assertEqual(_sha(self.path), before)
        self.assertEqual(
            _quarantines(self.dir, "permission_grants.json"), [])

    def test_healthy_round_trip_untouched(self):
        self.path.write_text(json.dumps(VALID_GRANTS), encoding="utf-8")
        self.assertEqual(_grants.get_state("microphone"), "granted")
        self.assertIsNone(_grants.corruption_state())
        self.assertTrue(_grants.set_permission_state(
            "camera", "denied", via="settings"))
        self.assertEqual(
            _quarantines(self.dir, "permission_grants.json"), [])
        now = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(now["grants"]["microphone"]["state"], "granted")
        self.assertEqual(now["grants"]["camera"]["state"], "denied")

    def test_missing_is_healthy_empty(self):
        self.assertEqual(_grants.get_state("microphone"), "denied")
        self.assertIsNone(_grants.corruption_state())
        self.assertEqual(_grants.corruption_notice(), "")

    def test_wrong_shape_is_corrupt(self):
        self.path.write_text(json.dumps({"grants": []}), encoding="utf-8")
        self.assertEqual(_grants.get_state("microphone"), "denied")
        record = _grants.corruption_state()
        self.assertIsNotNone(record)
        self.assertIn("wrong shape", record["error"])

    def test_quarantine_api(self):
        self.path.write_bytes(GARBAGE)
        _grants.get_state("microphone")
        name = _grants.quarantine_corrupt_store()
        self.assertIsNotNone(name)
        self.assertTrue(name.startswith("permission_grants.json.corrupt-"))
        kept = _quarantines(self.dir, "permission_grants.json")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]), hashlib.sha256(GARBAGE).hexdigest())
        self.assertIsNone(_grants.corruption_state())
        self.assertIsNone(_grants.quarantine_corrupt_store())

    def test_unreadable_store_disclosed_fail_closed(self):
        self.path.mkdir()
        self.assertEqual(_grants.get_state("microphone"), "denied")
        record = _grants.corruption_state()
        self.assertIsNotNone(record)
        self.assertIn("unreadable", record["error"])


# ---------------------------------------------------------------- settings
@unittest.skipUnless(_HAVE_QT, "settings QSettings legs need Qt")
class TestSettingsCorruptQt(_TempDirCase):
    def _store(self, content=None):
        """A temp conf + a QSettings over it. The content is
        written BEFORE the store is constructed: a QSettings
        object does not re-read a file that appears after it was
        built, so writing first is the only honest staging."""
        conf = self.dir / "Nova.conf"
        if content is not None:
            if isinstance(content, bytes):
                conf.write_bytes(content)
            else:
                conf.write_text(content, encoding="utf-8")
        return conf, _QSettings(str(conf), _QSettings.IniFormat)

    def tearDown(self):
        _reset = getattr(_config_spec, "reset_qsettings_corruption", None)
        if _reset is not None:
            _reset()
        super().tearDown()

    def test_corrupt_garbage_get_discloses(self):
        conf, store = self._store(GARBAGE)
        self.assertTrue(_config_spec.qsettings_get(
            "sounds_enabled", settings=store))
        record = _config_spec.qsettings_corruption_state(settings=store)
        self.assertIsNotNone(record)
        self.assertIn("Nova.conf", record["path"])
        self.assertIn("not a valid settings file", record["error"])
        notice = _config_spec.qsettings_corruption_notice(settings=store)
        self.assertIn("Nova.conf", notice)
        self.assertIn("Nothing was deleted", notice)

    def test_corrupt_set_quarantines_first(self):
        conf, store = self._store(GARBAGE)
        before = _sha(conf)
        _config_spec.qsettings_set(
            "sounds_enabled", False, settings=store)
        kept = _quarantines(self.dir, "Nova.conf")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]), before)
        self.assertFalse(_config_spec.qsettings_get(
            "sounds_enabled", settings=store))
        self.assertIsNone(
            _config_spec.qsettings_corruption_state(settings=store))

    def test_corrupt_remove_quarantines_first(self):
        conf, store = self._store(GARBAGE)
        before = _sha(conf)
        _config_spec.qsettings_remove("theme", settings=store)
        kept = _quarantines(self.dir, "Nova.conf")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]), before)

    def test_healthy_get_set_untouched(self):
        conf, store = self._store(HEALTHY_CONF)
        self.assertFalse(_config_spec.qsettings_get(
            "sounds_enabled", settings=store))
        self.assertIsNone(
            _config_spec.qsettings_corruption_state(settings=store))
        _config_spec.qsettings_set("theme", "Solar", settings=store)
        self.assertEqual(_quarantines(self.dir, "Nova.conf"), [])
        self.assertEqual(_config_spec.qsettings_get(
            "theme", settings=store), "Solar")
        self.assertFalse(_config_spec.qsettings_get(
            "sounds_enabled", settings=store))

    def test_missing_is_healthy_empty(self):
        conf, store = self._store()
        self.assertTrue(_config_spec.qsettings_get(
            "sounds_enabled", settings=store))
        self.assertIsNone(
            _config_spec.qsettings_corruption_state(settings=store))
        _config_spec.qsettings_set(
            "sounds_enabled", False, settings=store)
        self.assertTrue(conf.exists())
        self.assertEqual(_quarantines(self.dir, "Nova.conf"), [])

    def test_contains_discloses(self):
        conf, store = self._store(GARBAGE)
        self.assertFalse(_config_spec.qsettings_contains(
            "sounds_enabled", settings=store))
        self.assertIsNotNone(
            _config_spec.qsettings_corruption_state(settings=store))

    def test_truncated_conf_discloses(self):
        # Cut mid-key ("them", no delimiter) — a cut at a line
        # boundary or just after '=' would still parse, and a
        # file that parses is genuinely readable, not corrupt.
        # The readable first line still reads (False, not the
        # default True) while the damage is disclosed.
        conf, store = self._store(HEALTHY_CONF.encode()[:25])
        self.assertFalse(_config_spec.qsettings_get(
            "sounds_enabled", settings=store))
        self.assertIsNotNone(
            _config_spec.qsettings_corruption_state(settings=store))

    def test_quarantine_api(self):
        conf, store = self._store(GARBAGE)
        _config_spec.qsettings_get("sounds_enabled", settings=store)
        name = _config_spec.qsettings_quarantine_corrupt(settings=store)
        self.assertIsNotNone(name)
        self.assertTrue(name.startswith("Nova.conf.corrupt-"))
        kept = _quarantines(self.dir, "Nova.conf")
        self.assertEqual(len(kept), 1)
        self.assertEqual(_sha(kept[0]),
                         hashlib.sha256(GARBAGE).hexdigest())
        self.assertIsNone(
            _config_spec.qsettings_corruption_state(settings=store))
        self.assertIsNone(
            _config_spec.qsettings_quarantine_corrupt(settings=store))


class TestSettingsProbe(unittest.TestCase):
    """The conf-file probe is pure Python — runs on both."""

    def test_probe_judges_bytes(self):
        with tempfile.TemporaryDirectory(prefix="r3c3_probe_") as tmp:
            path = str(Path(tmp) / "Nova.conf")
            self.assertIsNone(
                _config_spec._qsettings_file_problem(path))
            Path(path).write_bytes(GARBAGE)
            _reset = getattr(
                _config_spec, "reset_qsettings_corruption", None)
            if _reset is not None:
                _reset()
            problem = _config_spec._qsettings_file_problem(path)
            self.assertIsNotNone(problem)
            self.assertIn("not a valid settings file", problem)
            Path(path).write_text(HEALTHY_CONF, encoding="utf-8")
            self.assertIsNone(
                _config_spec._qsettings_file_problem(path))
            Path(path).write_text("", encoding="utf-8")
            self.assertIsNone(
                _config_spec._qsettings_file_problem(path))

    def test_probe_healthy_cache_reprobes_on_change(self):
        with tempfile.TemporaryDirectory(prefix="r3c3_probe_") as tmp:
            path = Path(tmp) / "Nova.conf"
            path.write_text(HEALTHY_CONF, encoding="utf-8")
            self.assertIsNone(
                _config_spec._qsettings_file_problem(str(path)))
            path.write_bytes(GARBAGE)
            problem = _config_spec._qsettings_file_problem(str(path))
            self.assertIsNotNone(problem)


if __name__ == "__main__":
    unittest.main()
