"""Wallpaper-revert batch (2026-11-05): save-previous + revert.

Owner field report 2026-11-05: VYUK changed his wallpaper and he
could not revert it. Gap proved from the tree: no revert existed
anywhere, and neither set path saved the previous wallpaper, so a
revert had nothing to restore to.

THE ONE RULE (pinned here): the slot always holds the desktop
(image + style) as it was immediately before the most recent
VYUK wallpaper change. Revert restores that slot and NEVER
clears it, so reverting twice restores the same wallpaper
(idempotent); each new VYUK change overwrites the slot with the
then-current desktop, so revert after another VYUK change
restores the state before THAT change (the earlier one is gone);
a change made outside VYUK never touches the slot, so revert
still undoes the last VYUK change.

This suite pins, on both interpreters (Windows-only behaviour
is proved through a fake winreg + fake ctypes.windll — the lab
has no Windows desktop):

* save-before-apply records image + style + tile, atomically;
  a save failure blocks the change (fail-closed), seam never
  called;
* revert restores exactly the saved image + style; revert with
  nothing saved, with a deleted previous file, or with a
  previous that is a directory refuses honestly and keeps the
  record; a solid-colour previous ("") reverts via the same
  SPI call; revert twice is idempotent; a stale outside change
  does not move the slot; SPI refusal and style-write failure
  propagate honestly; read-back mismatch refuses;
* ALL THREE set paths save: apply_wallpaper, set_wallpaper
  (which also covers make_wallpaper), and display_control's
  set_wallpaper leg — one shared seam, not three copies;
* funnel: wallpaper_revert is confirm-gated (never read-only,
  never free), registry 655, snapshot entry {confirm True,
  free None, read False}; refused without approval with the
  seam never called; approved revert reports honestly;
* field validation on real Windows (actual desktop change,
  persistence flags, multi-monitor behaviour) is OWED — the
  lab fakes prove the decision/validation/wiring logic only,
  never a real desktop change.
"""

import ast
import contextlib
import ctypes
import json
import pathlib
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import confirm_gate, injection
from core import wallpaper as wallpaper_mod
from core import skills_desktop_prefs as prefs_mod
from core.desktop.control_plus_desktop import display_control
from core.funnel import tools_gate as tools_mod
from core.funnel import tools_gate_confirm as gate_confirm
from core.funnel import tools_gate_readonly as gate_readonly
from core.funnel.tools_registry import tools
from core.config import config as app_config
from core import catalogue_snapshot as catalogue_mod

_DATA_DIR = ROOT / "data"
_DESKTOP = r"Control Panel\Desktop"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class DataGuard:
    """Snapshot/restore the ENTIRE working data/ tree per test."""

    def __init__(self):
        self._saved = {}

    def _scan(self):
        found = {}
        for path in sorted(_DATA_DIR.rglob("*")):
            if path.is_file():
                try:
                    found[path.relative_to(_DATA_DIR)] = path.read_bytes()
                except OSError:
                    pass
        return found

    def __enter__(self):
        self._saved = self._scan()
        return self

    def __exit__(self, *exc):
        current = self._scan()
        for rel in current:
            if rel not in self._saved:
                try:
                    (_DATA_DIR / rel).unlink()
                except OSError:
                    pass
        for rel, data in self._saved.items():
            path = _DATA_DIR / rel
            try:
                if not path.exists() or path.read_bytes() != data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except OSError:
                pass
        return False


# ---------------------------------------------------------------------------
# Fake Windows: a winreg + ctypes.windll pair backed by one dict store.
# The fake SPI updates the store's Wallpaper value, exactly like
# real Windows with SPIF_UPDATEINIFILE does, so read-back tests
# are meaningful. update_registry=False models a lying SPI call
# (returns True, changes nothing) for the mismatch test.
# ---------------------------------------------------------------------------


class _FakeKey:
    def __init__(self, store, path):
        self._store = store
        self._path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set(self, name, value):
        self._store[(self._path, name)] = value

    def get(self, name):
        if (self._path, name) not in self._store:
            raise OSError("no value %r" % name)
        return (self._store[(self._path, name)], 1)


class _FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    REG_SZ = 1

    def __init__(self, store):
        self._store = store

    def CreateKeyEx(self, _root, key_path):
        return _FakeKey(self._store, key_path)

    def OpenKey(self, _root, key_path):
        return _FakeKey(self._store, key_path)

    def SetValueEx(self, key, name, _reserved, _type, value):
        key.set(name, value)

    def QueryValueEx(self, key, name):
        return key.get(name)


class _FakeUser32:
    def __init__(self, store, ok=True, update_registry=True):
        self._store = store
        self.ok = ok
        self.update_registry = update_registry
        self.calls = []

    def SystemParametersInfoW(self, _spi, _p, path, _flags):
        self.calls.append(str(path))
        if self.ok and self.update_registry:
            self._store[(_DESKTOP, "Wallpaper")] = str(path)
        return self.ok


def _seed(store, image, style="10", tile="0"):
    store[(_DESKTOP, "Wallpaper")] = image
    store[(_DESKTOP, "WallpaperStyle")] = style
    store[(_DESKTOP, "TileWallpaper")] = tile


@contextlib.contextmanager
def _fake_windows(store, ok=True, update_registry=True):
    """Patch wallpaper.is_windows True + fake winreg + fake windll."""
    fake_reg = _FakeWinreg(store)
    fake_u32 = _FakeUser32(store, ok=ok,
                           update_registry=update_registry)
    holder = types.SimpleNamespace(user32=fake_u32)
    with mock.patch.object(wallpaper_mod, "is_windows",
                           return_value=True), \
            mock.patch.dict(sys.modules, {"winreg": fake_reg}), \
            mock.patch.object(ctypes, "windll", holder,
                              create=True):
        yield fake_u32


@contextlib.contextmanager
def _record_path(tmp):
    """Point the revert slot at a scratch file for one test."""
    path = pathlib.Path(tmp) / "wallpaper_previous.json"
    with mock.patch.object(wallpaper_mod, "_PATH_OVERRIDE", path):
        yield path


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


def _read_record(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


class FunnelBase(unittest.TestCase):
    def setUp(self):
        self._guard = DataGuard()
        self._guard.__enter__()
        injection.clear_taint(force=True)
        confirm_gate.clear_handler()
        self._orig_prov = tools_mod.session_provenance()
        tools_mod.set_session_provenance("user")
        self._orig_lock = getattr(app_config,
                                  "session_provenance_lock", False)
        app_config.session_provenance_lock = False

    def tearDown(self):
        confirm_gate.clear_handler()
        injection.clear_taint(force=True)
        tools_mod.set_session_provenance(self._orig_prov)
        app_config.session_provenance_lock = self._orig_lock
        self._guard.__exit__()

    def call(self, name, args, approve=False):
        confirm_gate.clear_handler()
        if approve:
            confirm_gate.register_handler(lambda n, s: True)
        try:
            return tools.execute(name, dict(args))
        finally:
            confirm_gate.clear_handler()


class TestSavePrevious(unittest.TestCase):
    def test_save_records_image_style_tile(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _record_path(tmp) as record:
                with mock.patch.object(
                        wallpaper_mod, "_read_current_wallpaper",
                        return_value={"image": r"C:\Users\x\old.png",
                                      "style": "6", "tile": "0"}), \
                        mock.patch.object(wallpaper_mod, "is_windows",
                                          return_value=True):
                    result = wallpaper_mod.save_previous_wallpaper()
            self.assertEqual(result, {"saved": True, "reason": "ok"})
            saved = _read_record(record)
            self.assertEqual(saved["image"], r"C:\Users\x\old.png")
            self.assertEqual(saved["style"], "6")
            self.assertEqual(saved["tile"], "0")
            # R4 INTENTIONAL (version 1 -> 2): the flat fields
            # stay the global record; "monitors" rides alongside
            # when per-monitor records exist.
            self.assertEqual(saved["version"], 2)

    def test_save_fails_when_current_unreadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _record_path(tmp) as record:
                with mock.patch.object(
                        wallpaper_mod, "_read_current_wallpaper",
                        return_value=None), \
                        mock.patch.object(wallpaper_mod, "is_windows",
                                          return_value=True):
                    result = wallpaper_mod.save_previous_wallpaper()
            self.assertEqual(result["saved"], False)
            self.assertEqual(result["reason"], "could-not-read-current")
            self.assertFalse(pathlib.Path(record).exists())

    def test_save_windows_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _record_path(tmp) as record:
                with mock.patch.object(wallpaper_mod, "is_windows",
                                       return_value=False):
                    result = wallpaper_mod.save_previous_wallpaper()
            self.assertEqual(result, {"saved": False,
                                      "reason": "windows-only"})
            self.assertFalse(pathlib.Path(record).exists())

    def test_save_through_fake_windows_reads_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = _write(pathlib.Path(tmp) / "old.png", PNG_BYTES)
            store = {}
            _seed(store, old, style="0", tile="1")
            with _record_path(tmp) as record, _fake_windows(store):
                result = wallpaper_mod.save_previous_wallpaper()
            self.assertEqual(result, {"saved": True, "reason": "ok"})
            saved = _read_record(record)
            # R4 INTENTIONAL (version 1 -> 2): the flat fields
            # stay the global record; "monitors" rides alongside
            # when per-monitor records exist.
            self.assertEqual(saved, {"version": 2, "image": old,
                                     "style": "0", "tile": "1"})

    def test_load_none_when_missing_corrupt_wrong_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _record_path(tmp) as record:
                self.assertIsNone(
                    wallpaper_mod.load_previous_wallpaper())
                pathlib.Path(record).write_text(
                    "{not json", encoding="utf-8")
                self.assertIsNone(
                    wallpaper_mod.load_previous_wallpaper())
                pathlib.Path(record).write_text(
                    json.dumps({"image": 42, "style": "10",
                                "tile": "0"}), encoding="utf-8")
                self.assertIsNone(
                    wallpaper_mod.load_previous_wallpaper())
                pathlib.Path(record).write_text(
                    json.dumps({"image": "", "style": "10",
                                "tile": "0"}), encoding="utf-8")
                self.assertEqual(
                    wallpaper_mod.load_previous_wallpaper(),
                    {"image": "", "style": "10", "tile": "0"})


class TestRevertUnit(unittest.TestCase):
    def _round_trip(self, tmp, old_style="10", old_tile="0",
                    new_style="6", new_tile="0"):
        """Seed old desktop, save it, change to a new one; return
        (store, record path, old path, new path)."""
        old = _write(pathlib.Path(tmp) / "old.png", PNG_BYTES)
        new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
        store = {}
        _seed(store, old, style=old_style, tile=old_tile)
        return store, old, new

    def test_revert_restores_saved_image_and_style(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, old, new = self._round_trip(tmp, old_style="0",
                                               old_tile="1")
            with _record_path(tmp) as record, _fake_windows(store) as u32:
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
                _seed(store, new, style="6", tile="0")
                result = wallpaper_mod.revert_wallpaper()
                kept = wallpaper_mod.load_previous_wallpaper()
            self.assertEqual(result["reverted"], True, result)
            self.assertEqual(result["reason"], "ok")
            self.assertEqual(result["image"], old)
            self.assertEqual(result["style"], "0")
            self.assertEqual(u32.calls, [old])
            self.assertEqual(store[(_DESKTOP, "Wallpaper")], old)
            self.assertEqual(store[(_DESKTOP, "WallpaperStyle")], "0")
            self.assertEqual(store[(_DESKTOP, "TileWallpaper")], "1")
            # The slot is never cleared by a revert.
            self.assertEqual(kept["image"], old)

    def test_revert_nothing_saved_refuses_honestly(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "new.png",
                                PNG_BYTES))
            with _record_path(tmp), _fake_windows(store) as u32:
                result = wallpaper_mod.revert_wallpaper()
            self.assertEqual(result, {"reverted": False,
                                      "reason": "no-previous-saved"})
            self.assertEqual(u32.calls, [])

    def test_revert_deleted_previous_refuses_and_keeps_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, old, new = self._round_trip(tmp)
            with _record_path(tmp) as record, _fake_windows(store) as u32:
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
                pathlib.Path(old).unlink()
                _seed(store, new)
                result = wallpaper_mod.revert_wallpaper()
                kept = wallpaper_mod.load_previous_wallpaper()
            self.assertEqual(result, {"reverted": False,
                                      "reason": "previous-file-missing"})
            self.assertEqual(u32.calls, [])
            # Record kept.
            self.assertEqual(kept["image"], old)

    def test_revert_previous_is_directory_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = pathlib.Path(tmp) / "adir"
            sub.mkdir()
            with _record_path(tmp) as record, _fake_windows({}) as u32:
                pathlib.Path(record).write_text(
                    json.dumps({"version": 1, "image": str(sub),
                                "style": "10", "tile": "0"}),
                    encoding="utf-8")
                result = wallpaper_mod.revert_wallpaper()
            self.assertEqual(result, {"reverted": False,
                                      "reason": "previous-not-a-file"})
            self.assertEqual(u32.calls, [])

    def test_revert_solid_colour_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "new.png",
                                PNG_BYTES))
            with _record_path(tmp) as record, _fake_windows(store) as u32:
                pathlib.Path(record).write_text(
                    json.dumps({"version": 1, "image": "",
                                "style": "10", "tile": "0"}),
                    encoding="utf-8")
                result = wallpaper_mod.revert_wallpaper()
            self.assertEqual(result["reverted"], True, result)
            self.assertEqual(result["image"], "")
            self.assertEqual(u32.calls, [""])
            self.assertEqual(store[(_DESKTOP, "Wallpaper")], "")

    def test_revert_twice_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, old, new = self._round_trip(tmp)
            with _record_path(tmp), _fake_windows(store) as u32:
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
                _seed(store, new)
                first = wallpaper_mod.revert_wallpaper()
                second = wallpaper_mod.revert_wallpaper()
            self.assertTrue(first["reverted"])
            self.assertTrue(second["reverted"])
            self.assertEqual(first["image"], second["image"])
            self.assertEqual(u32.calls, [old, old])

    def test_revert_after_another_change_restores_last_change(self):
        # THE ONE RULE: revert restores the state before the most
        # recent VYUK change — not the original.
        with tempfile.TemporaryDirectory() as tmp:
            a = _write(pathlib.Path(tmp) / "a.png", PNG_BYTES)
            b = _write(pathlib.Path(tmp) / "b.png", PNG_BYTES)
            c = _write(pathlib.Path(tmp) / "c.png", PNG_BYTES)
            d = _write(pathlib.Path(tmp) / "d.png", PNG_BYTES)
            store = {}
            _seed(store, a)
            with _record_path(tmp), _fake_windows(store) as u32:
                self.assertTrue(
                    wallpaper_mod.apply_wallpaper(b)["applied"])
                _seed(store, c)  # changed outside VYUK
                self.assertTrue(
                    wallpaper_mod.apply_wallpaper(d)["applied"])
                result = wallpaper_mod.revert_wallpaper()
            self.assertTrue(result["reverted"], result)
            self.assertEqual(result["image"], c)
            self.assertEqual(u32.calls, [b, d, c])

    def test_revert_ignores_outside_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, old, new = self._round_trip(tmp)
            with _record_path(tmp), _fake_windows(store) as u32:
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
                outside = _write(pathlib.Path(tmp) / "outside.png",
                                 PNG_BYTES)
                _seed(store, outside)  # changed outside VYUK
                result = wallpaper_mod.revert_wallpaper()
            self.assertTrue(result["reverted"], result)
            self.assertEqual(result["image"], old)
            self.assertEqual(u32.calls, [old])

    def test_revert_spi_refusal_propagates(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, old, new = self._round_trip(tmp)
            with _record_path(tmp), _fake_windows(store, ok=False) as u32:
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
                _seed(store, new)
                result = wallpaper_mod.revert_wallpaper()
            self.assertEqual(result, {"reverted": False,
                                      "reason": "system-call-refused"})
            self.assertEqual(u32.calls, [old])

    def test_revert_style_write_failure_blocks_spi(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, old, new = self._round_trip(tmp)
            with _record_path(tmp), _fake_windows(store) as u32:
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
                _seed(store, new)
                with mock.patch.object(
                        wallpaper_mod, "_write_wallpaper_style",
                        return_value=False):
                    result = wallpaper_mod.revert_wallpaper()
            self.assertEqual(result, {"reverted": False,
                                      "reason":
                                          "could-not-restore-style"})
            self.assertEqual(u32.calls, [])

    def test_revert_read_back_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            store, old, new = self._round_trip(tmp)
            with _record_path(tmp), \
                    _fake_windows(store,
                                  update_registry=False) as u32:
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
                _seed(store, new)
                result = wallpaper_mod.revert_wallpaper()
            self.assertEqual(result["reverted"], False, result)
            self.assertEqual(result["reason"], "read-back-mismatch")
            self.assertEqual(u32.calls, [old])

    def test_revert_windows_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            with _record_path(tmp), \
                    mock.patch.object(wallpaper_mod, "is_windows",
                                      return_value=False):
                result = wallpaper_mod.revert_wallpaper()
            self.assertEqual(result, {"reverted": False,
                                      "reason": "windows-only"})


class TestApplySaves(unittest.TestCase):
    def test_apply_saves_before_spi(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = _write(pathlib.Path(tmp) / "old.png", PNG_BYTES)
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, old, style="6", tile="0")
            with _record_path(tmp) as record, _fake_windows(store) as u32:
                result = wallpaper_mod.apply_wallpaper(new)
            self.assertEqual(result, {"applied": True, "reason": "ok"})
            self.assertEqual(u32.calls, [new])
            saved = _read_record(record)
            self.assertEqual(saved["image"], old)
            self.assertEqual(saved["style"], "6")
            self.assertEqual(saved["tile"], "0")

    def test_apply_save_failure_blocks_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            calls = []
            with mock.patch.object(wallpaper_mod, "is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "_read_current_wallpaper",
                        return_value=None), \
                    mock.patch.object(
                        wallpaper_mod, "_set_wallpaper_via_spi",
                        side_effect=lambda p: calls.append(p) or True):
                result = wallpaper_mod.apply_wallpaper(new)
            self.assertEqual(result, {"applied": False,
                                      "reason": "could-not-read-current"})
            self.assertEqual(calls, [])


class TestSetWallpaperSaves(unittest.TestCase):
    def test_set_wallpaper_saves_before_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = _write(pathlib.Path(tmp) / "old.png", PNG_BYTES)
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, old, style="10", tile="0")
            with _record_path(tmp) as record, _fake_windows(store) as u32, \
                    mock.patch.object(prefs_mod, "_is_windows",
                                      return_value=True):
                result = prefs_mod.set_wallpaper(new, "fit")
            self.assertIn("Wallpaper set to", result, result)
            self.assertIn("style: fit", result, result)
            self.assertEqual(u32.calls, [new])
            saved = _read_record(record)
            self.assertEqual(saved["image"], old)
            self.assertEqual(saved["style"], "10")
            self.assertEqual(saved["tile"], "0")
            self.assertEqual(store[(_DESKTOP, "Wallpaper")], new)
            self.assertEqual(store[(_DESKTOP, "WallpaperStyle")], "6")
            self.assertEqual(store[(_DESKTOP, "TileWallpaper")], "0")

    def test_set_wallpaper_save_failure_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "old.png",
                                PNG_BYTES))
            with mock.patch.object(prefs_mod, "_is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "save_previous_wallpaper",
                        return_value={"saved": False,
                                      "reason":
                                          "could-not-save-previous"}), \
                    _fake_windows(store) as u32:
                result = prefs_mod.set_wallpaper(new, "fit")
            # B1 (INTENTIONAL): a failed set returns a funnel-shaped
            # failure dict now, never a plain string — the message
            # text is unchanged.
            self.assertEqual(result["status"], "failure", result)
            self.assertEqual(result["kind"], "wallpaper_not_set", result)
            self.assertIn("Could not save the current wallpaper",
                          result["message"], result)
            self.assertIn("left unchanged", result["message"], result)
            self.assertEqual(u32.calls, [])
            self.assertEqual(store[(_DESKTOP, "WallpaperStyle")], "10")

    def test_set_wallpaper_non_windows_never_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            calls = []
            with mock.patch.object(
                    wallpaper_mod, "save_previous_wallpaper",
                    side_effect=lambda: calls.append(1) or
                    {"saved": True, "reason": "ok"}):
                result = prefs_mod.set_wallpaper(new, "fit")
            self.assertIn("needs Windows", result, result)
            self.assertEqual(calls, [])


class TestDisplayControlSaves(unittest.TestCase):
    def test_display_control_saves_before_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = _write(pathlib.Path(tmp) / "old.png", PNG_BYTES)
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, old, style="6", tile="0")
            import core.desktop.control_plus_desktop as dc_mod

            with _record_path(tmp) as record, _fake_windows(store) as u32, \
                    mock.patch.object(dc_mod, "_is_windows",
                                      return_value=True):
                result = display_control("set_wallpaper", path=new)
            # INTENTIONAL (R1a): the set_wallpaper leg now returns an
            # Outcome (verified by the H10 REG_SZ read-back), so the
            # message assertion reads it through str(...).
            self.assertIn("Wallpaper set to", str(result), result)
            # R1a: the success is a verified Outcome, not a bare string.
            self.assertTrue(getattr(result, "verified", False))
            self.assertEqual(u32.calls, [new])
            saved = _read_record(record)
            self.assertEqual(saved["image"], old)
            self.assertEqual(saved["style"], "6")
            # This leg writes no style — the current style survives.
            self.assertEqual(store[(_DESKTOP, "WallpaperStyle")], "6")
            self.assertEqual(store[(_DESKTOP, "TileWallpaper")], "0")

    def test_display_control_save_failure_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "old.png",
                                PNG_BYTES))
            import core.desktop.control_plus_desktop as dc_mod

            with mock.patch.object(dc_mod, "_is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "save_previous_wallpaper",
                        return_value={"saved": False,
                                      "reason":
                                          "could-not-save-previous"}), \
                    _fake_windows(store) as u32:
                result = display_control("set_wallpaper", path=new)
            # B1 (INTENTIONAL): a failed set returns a funnel-shaped
            # failure dict now, never a plain string — the message
            # text is unchanged.
            self.assertEqual(result["status"], "failure", result)
            self.assertEqual(result["kind"], "wallpaper_not_set", result)
            self.assertIn("Could not save the current wallpaper",
                          result["message"], result)
            self.assertIn("left unchanged", result["message"], result)
            self.assertEqual(u32.calls, [])


class TestSetFailureDicts(FunnelBase):
    """B1 (2026-11-06): every set failure leaves the funnel as a
    failure dict (kind wallpaper_not_set), never as a success
    payload — on BOTH executor paths, for every reason the
    shared seam can produce and every legacy shape. The
    message text is the same sentence the handlers always
    returned; only the envelope changes. A genuine set still
    returns success."""

    _REASONS = (
        "unknown-monitor: no monitor named second monitor "
        "(monitors now: primary DISPLAY1)",
        "art-missing",
        "could-not-save-previous",
        "could-not-read-current",
        "system-call-refused",
        "system-call-failed: RuntimeError",
        "read-back-mismatch",
        "windows-only",
    )

    def _seam(self, reason):
        return {"applied": False, "reason": reason, "fell_back": False,
                "monitor": "second monitor", "per_monitor": {}}

    def test_every_seam_reason_is_a_failure_dict_on_both_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "old.png",
                                PNG_BYTES))
            for reason in self._REASONS:
                with self.subTest(reason=reason):
                    with mock.patch.object(
                            prefs_mod, "_is_windows",
                            return_value=True), \
                            mock.patch.object(
                                wallpaper_mod,
                                "set_wallpaper_for_monitors",
                                return_value=self._seam(reason)), \
                            _fake_windows(store):
                        import core.desktop.control_plus_desktop as dc_mod
                        with mock.patch.object(
                                dc_mod, "_is_windows",
                                return_value=True):
                            dc = display_control(
                                "set_wallpaper", path=new,
                                monitor="second monitor")
                        sw = prefs_mod.set_wallpaper(
                            new, "fill", monitor="second monitor")
                    for result in (dc, sw):
                        self.assertEqual(result["status"], "failure",
                                         result)
                        self.assertEqual(result["kind"],
                                         "wallpaper_not_set", result)
                        self.assertIn(
                            "Wallpaper not set: %s." % reason,
                            result["message"], result)

    def test_funnel_wraps_both_paths_as_failure(self):
        # Through the real funnel, not the direct call: the
        # audited dict is a failure, so the coordinator can
        # never record it as success.
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "old.png",
                                PNG_BYTES))
            with mock.patch.object(
                    prefs_mod, "_is_windows", return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "set_wallpaper_for_monitors",
                        return_value=self._seam("art-missing")), \
                    _fake_windows(store):
                import core.desktop.control_plus_desktop as dc_mod
                with mock.patch.object(dc_mod, "_is_windows",
                                      return_value=True):
                    dc = self.call(
                        "display_control",
                        {"action": "set_wallpaper", "path": new,
                         "monitor": "second monitor"})
                sw = self.call(
                    "set_wallpaper",
                    {"path": new, "style": "fill",
                     "monitor": "second monitor"})
            for result in (dc, sw):
                self.assertEqual(result["status"], "failure", result)
                self.assertEqual(result["kind"], "wallpaper_not_set",
                                 result)
                self.assertIn("Wallpaper not set: art-missing.",
                              result["message"], result)

    def test_legacy_shapes_are_failure_dicts_on_both_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "old.png",
                                PNG_BYTES))
            import core.desktop.control_plus_desktop as dc_mod
            # Legacy SPI refusal (ok=False).
            with mock.patch.object(prefs_mod, "_is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "save_previous_wallpaper",
                        return_value={"saved": True, "reason": "ok"}), \
                    _fake_windows(store, ok=False), \
                    mock.patch.object(dc_mod, "_is_windows",
                                      return_value=True):
                dc = display_control("set_wallpaper", path=new)
                sw = prefs_mod.set_wallpaper(new, "fit")
            self.assertEqual(dc["status"], "failure", dc)
            self.assertIn("Windows refused the wallpaper change",
                          dc["message"], dc)
            self.assertEqual(sw["status"], "failure", sw)
            self.assertIn("Windows refused the new wallpaper",
                          sw["message"], sw)
            # Legacy read-back mismatch (SPI ok, registry stale).
            store[(_DESKTOP, "Wallpaper")] = r"C:\Users\x\old.png"
            with mock.patch.object(prefs_mod, "_is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "save_previous_wallpaper",
                        return_value={"saved": True, "reason": "ok"}), \
                    _fake_windows(store, ok=True,
                                  update_registry=False), \
                    mock.patch.object(dc_mod, "_is_windows",
                                      return_value=True):
                dc = display_control("set_wallpaper", path=new)
                sw = prefs_mod.set_wallpaper(new, "fit")
            for result in (dc, sw):
                self.assertEqual(result["status"], "failure", result)
                self.assertEqual(result["kind"], "wallpaper_not_set",
                                 result)
                self.assertIn("Windows accepted the wallpaper change",
                              result["message"], result)

    def test_genuine_success_still_success_on_both_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, _write(pathlib.Path(tmp) / "old.png",
                                PNG_BYTES))
            import core.desktop.control_plus_desktop as dc_mod
            with mock.patch.object(prefs_mod, "_is_windows",
                                   return_value=True), \
                    mock.patch.object(
                        wallpaper_mod, "set_wallpaper_for_monitors",
                        return_value={"applied": True, "reason": "ok",
                                      "fell_back": False,
                                      "monitor": "primary",
                                      "per_monitor": {}}), \
                    _fake_windows(store), \
                    mock.patch.object(dc_mod, "_is_windows",
                                      return_value=True):
                dc = display_control("set_wallpaper", path=new,
                                     monitor="primary")
                sw = prefs_mod.set_wallpaper(new, "fill",
                                             monitor="primary")
            self.assertIn("Wallpaper set to", str(dc), dc)
            self.assertTrue(getattr(dc, "verified", False))
            self.assertIn("Wallpaper set to", sw, sw)


class TestRevertFunnel(FunnelBase):
    def test_gate_class_registry_and_snapshot(self):
        self.assertIn("wallpaper_revert",
                      gate_confirm._CONFIRM_GATE_TOOLS)
        self.assertTrue(
            tools_mod._needs_confirmation("wallpaper_revert", {}))
        self.assertNotIn("wallpaper_revert",
                         gate_readonly._READ_ONLY_TOOLS)
        self.assertNotIn("wallpaper_revert",
                         tools_mod._FREE_BY_DESIGN)
        self.assertEqual(len(tools.get_declarations()), 756)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        snap = json.loads(
            (ROOT / "tests" / "_classification_snapshot.json"
             ).read_text(encoding="utf-8"))
        self.assertEqual(
            snap["wallpaper_revert"],
            {"confirm": True, "free": None, "read": False})
        # wallpaper_state keeps its read-only class untouched.
        self.assertTrue(
            tools_mod._is_read_class("wallpaper_state", {}))
        self.assertFalse(
            tools_mod._needs_confirmation("wallpaper_state", {}))

    def test_refused_without_approval_seam_never_called(self):
        calls = []
        with mock.patch.object(
                wallpaper_mod, "revert_wallpaper",
                side_effect=lambda: calls.append(1) or
                {"reverted": True, "reason": "ok", "image": "x",
                 "style": "fill"}):
            refused = self.call("wallpaper_revert", {})
        self.assertEqual(refused["status"], "refused", refused)
        self.assertEqual(refused["kind"], "not_confirmed")
        self.assertEqual(calls, [])

    def test_approved_revert_success(self):
        with mock.patch.object(
                wallpaper_mod, "revert_wallpaper",
                return_value={"reverted": True, "reason": "ok",
                              "image": r"C:\Users\x\old.png",
                              "style": "fill"}):
            result = self.call("wallpaper_revert", {}, approve=True)
        self.assertEqual(result["status"], "success", result)
        self.assertIn("Wallpaper reverted to", result["result"])
        self.assertIn(r"C:\Users\x\old.png", result["result"])
        self.assertIn("immediately before the last change",
                      result["result"])

    def test_approved_no_previous_saved_is_honest(self):
        with mock.patch.object(
                wallpaper_mod, "revert_wallpaper",
                return_value={"reverted": False,
                              "reason": "no-previous-saved"}):
            result = self.call("wallpaper_revert", {}, approve=True)
        self.assertEqual(result["status"], "success", result)
        self.assertIn("No previous wallpaper was saved",
                      result["result"])
        self.assertIn("Personalization > Background", result["result"])

    def test_approved_previous_file_missing_is_honest(self):
        with mock.patch.object(
                wallpaper_mod, "revert_wallpaper",
                return_value={"reverted": False,
                              "reason": "previous-file-missing"}):
            result = self.call("wallpaper_revert", {}, approve=True)
        self.assertEqual(result["status"], "success", result)
        self.assertIn("no longer exists", result["result"])
        self.assertIn("kept", result["result"])

    def test_seam_wiring_pinned_in_source(self):
        # Pins the production wiring the lab cannot execute: all
        # three set paths call the shared save seam before any
        # Windows write, and the revert tool is registered +
        # confirm-gated. The actual desktop change is field work.
        wall_src = (ROOT / "core" / "wallpaper.py").read_text()
        tree = ast.parse(wall_src)
        apply_fn = next(n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef)
                        and n.name == "apply_wallpaper")
        self.assertIn("save_previous_wallpaper",
                      ast.get_source_segment(wall_src, apply_fn))
        prefs_src = (ROOT / "core" / "skills_desktop_prefs.py"
                     ).read_text()
        prefs_tree = ast.parse(prefs_src)
        set_fn = next(n for n in ast.walk(prefs_tree)
                      if isinstance(n, ast.FunctionDef)
                      and n.name == "set_wallpaper")
        self.assertIn("save_previous_wallpaper",
                      ast.get_source_segment(prefs_src, set_fn))
        dc_src = (ROOT / "core" / "desktop" / "control_plus_desktop.py"
                  ).read_text()
        self.assertIn("save_previous_wallpaper", dc_src)
        lib_src = (ROOT / "core" / "funnel"
                   / "tools_handlers_pack_libwire.py").read_text()
        self.assertIn('"wallpaper_revert"', lib_src)
        self.assertIn("_tool_wallpaper_revert", lib_src)
        gate_src = (ROOT / "core" / "funnel"
                    / "tool_classification.py").read_text()
        self.assertIn('"wallpaper_revert"', gate_src)
        # Catalogue: revert sits in the same display group as apply.
        groups = catalogue_mod.GROUP_MEMBERS
        group = next(k for k, v in groups.items()
                     if "wallpaper_apply" in v)
        self.assertIn("wallpaper_revert", groups[group])

    def test_pilot_status_revert_built_field_validation_still_owed(self):
        status = wallpaper_mod.pilot_status()
        self.assertEqual(status["disposition"], "PILOT")
        self.assertTrue(status["wired"])
        self.assertIn("field validation", status["closing_step"])
        self.assertIn("revert", status["closing_step"])


if __name__ == "__main__":
    unittest.main()
