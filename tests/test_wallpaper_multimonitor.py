"""Wallpaper multi-monitor batch (R4, 2026-11-06): per-monitor set + revert.

Fix 2 (2026-11-05) proved the gap from the tree: SPI_SETDESKWALLPAPER
sets ONE wallpaper on ALL monitors, and the revert slot was one
global record. This suite pins the per-monitor half, on both
interpreters (Windows-only behaviour is proved through a fake
winreg + fake ctypes.windll + a fake IDesktopWallpaper + a fake
monitor-table backend — the lab has no Windows desktop):

* setting one monitor leaves the others untouched (the COM seam
  is called once, with the right device name) and saves THAT
  monitor's previous first; the global record is not moved;
* reverting one monitor restores only its record and never
  clears it (revert twice is idempotent per monitor);
* a monitor that vanished between set and revert is reported
  ("monitor-gone") and skipped — revert "all" still reverts
  the monitors that remain, as a partial result;
* a newly attached monitor inherits nothing: records are keyed
  by device name, never by position, so it has no record and
  refuses honestly;
* solid-colour and slideshow monitors round-trip verbatim
  (kind + colour + engine state); a slideshow revert verifies
  the ENGINE state, not the advancing frame;
* single-monitor behaviour is unchanged: monitor=None is the
  legacy SPI path (same calls, same result); "primary" / "all"
  on one monitor set that one monitor;
* per-monitor API unavailable falls back to SPI on all monitors
  and SAYS so; per-monitor API refusing is an honest failure
  with NO fallback (falling back would change ALL monitors
  when one was asked for);
* ALL THREE set paths take a monitor: apply_wallpaper,
  set_wallpaper, display_control's set_wallpaper leg — one
  shared seam, not three copies; the funnel tools carry an
  optional "monitor" property (registry stays 655, schema
  total 1482 -> 1486 INTENTIONAL);
* the v1 flat record still reads (global revert works from it);
  a global save preserves per-monitor records.

Field validation on real Windows (actual desktop change on a
real multi-monitor host, monitor vanish/attach on real
hardware, the exact IDesktopWallpaper semantics) is OWED — the
lab fakes prove the decision/validation/wiring logic only,
never a real desktop change.
"""

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
from core import wallpaper_monitors as monitors_mod
from core import skills_desktop_prefs as prefs_mod
from core.desktop.control_plus_desktop import display_control
from core.funnel import tools_gate as tools_mod
from core.funnel import tools_gate_confirm as gate_confirm
from core.funnel import tools_gate_readonly as gate_readonly
from core.funnel.tools_registry import tools
from core.config import config as app_config

_DATA_DIR = ROOT / "data"
_DESKTOP = r"Control Panel\Desktop"
_SLIDESHOW = r"Control Panel\Personalization\Desktop Slideshow"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

DISPLAY1 = r"\\.\DISPLAY1"
DISPLAY2 = r"\\.\DISPLAY2"
DISPLAY3 = r"\\.\DISPLAY3"


def _two_monitors():
    return [
        {"monitor_id": DISPLAY1, "device": DISPLAY1, "primary": True,
         "left": 0, "top": 0, "width": 1920, "height": 1080,
         "scale": 1.0},
        {"monitor_id": DISPLAY2, "device": DISPLAY2, "primary": False,
         "left": 1920, "top": 0, "width": 1920, "height": 1080,
         "scale": 1.0},
    ]


def _one_monitor():
    return [_two_monitors()[0]]


class _FakeBackend:
    """Monitor-table backend: enumerate() returns the table."""

    def __init__(self, table):
        self._table = table

    def enumerate(self):
        return [dict(m) for m in self._table]


class _FakeDesktopWallpaper:
    """IDesktopWallpaper fake: per-monitor images + colour +
    slideshow engine state. Records every call."""

    def __init__(self, images=None, color="#000000", slideshow=None):
        self.images = dict(images or {})
        self.color = color
        self.slideshow = dict(
            slideshow or {"active": False, "root": "",
                          "interval_ms": 0, "shuffle": False})
        self.calls = []
        self.refuse_set = False

    def release(self):
        pass

    def set_wallpaper(self, monitor_id, image):
        self.calls.append(("set", monitor_id, image))
        if self.refuse_set:
            return False
        self.images[monitor_id] = image
        return True

    def get_wallpaper(self, monitor_id):
        self.calls.append(("get", monitor_id))
        return self.images.get(monitor_id)

    def get_background_color(self):
        return self.color

    def set_background_color(self, color):
        self.calls.append(("color", color))
        self.color = color
        return True

    def get_slideshow_options(self):
        return (1 if self.slideshow.get("shuffle") else 0,
                int(self.slideshow.get("interval_ms") or 0))

    def set_slideshow_options(self, options, tick_ms):
        self.calls.append(("slideshow-options", options, tick_ms))
        self.slideshow["shuffle"] = bool(options & 1)
        self.slideshow["interval_ms"] = int(tick_ms)
        return True

    def get_status(self):
        active = self.slideshow.get("active")
        return (0x01 | 0x02) if active else 0

    def enable_slideshow(self, enable):
        self.calls.append(("enable", bool(enable)))
        self.slideshow["active"] = bool(enable)
        return True


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
    REG_DWORD = 4

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
    def __init__(self, store, ok=True):
        self._store = store
        self.ok = ok
        self.calls = []

    def SystemParametersInfoW(self, _spi, _p, path, _flags):
        self.calls.append(str(path))
        if self.ok:
            self._store[(_DESKTOP, "Wallpaper")] = str(path)
        return self.ok


def _seed(store, image, style="10", tile="0"):
    store[(_DESKTOP, "Wallpaper")] = image
    store[(_DESKTOP, "WallpaperStyle")] = style
    store[(_DESKTOP, "WallpaperTile")] = tile
    store[(_DESKTOP, "TileWallpaper")] = tile


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


@contextlib.contextmanager
def _record_path(tmp):
    path = pathlib.Path(tmp) / "wallpaper_previous.json"
    with mock.patch.object(wallpaper_mod, "_PATH_OVERRIDE", path):
        yield path


@contextlib.contextmanager
def _fake_windows(store, ok=True, backend=None, com=None):
    """Patch is_windows True + fake winreg + fake windll +
    fake monitor table + fake IDesktopWallpaper."""
    fake_reg = _FakeWinreg(store)
    fake_u32 = _FakeUser32(store, ok=ok)
    holder = types.SimpleNamespace(user32=fake_u32)
    fake_com = com if com is not None else _FakeDesktopWallpaper()
    with mock.patch.object(wallpaper_mod, "is_windows",
                           return_value=True), \
            mock.patch.object(monitors_mod, "is_windows",
                              return_value=True), \
            mock.patch.object(monitors_mod, "_TABLE_BACKEND",
                              backend), \
            mock.patch.object(monitors_mod, "_get_desktop_wallpaper",
                              return_value=fake_com), \
            mock.patch.dict(sys.modules, {"winreg": fake_reg}), \
            mock.patch.object(ctypes, "windll", holder,
                              create=True):
        yield fake_u32, fake_com


def _read_record(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _write_file(path, data=b""):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return str(path)


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


class TestResolver(unittest.TestCase):
    def test_plain_language_selectors_resolve(self):
        backend = _FakeBackend(_two_monitors())
        with mock.patch.object(monitors_mod, "_TABLE_BACKEND", backend):
            for selector, want in (
                    ("primary", DISPLAY1),
                    ("left", DISPLAY1),
                    ("right", DISPLAY2),
                    ("second monitor", DISPLAY2),
                    ("DISPLAY2", DISPLAY2),
                    (DISPLAY1, DISPLAY1)):
                monitor, refusal, is_all = (
                    monitors_mod.resolve_wallpaper_monitor(selector))
                self.assertEqual(refusal, "", selector)
                self.assertFalse(is_all, selector)
                self.assertEqual(
                    monitor.get("monitor_id"), want, selector)

    def test_all_and_none_selectors(self):
        backend = _FakeBackend(_two_monitors())
        with mock.patch.object(monitors_mod, "_TABLE_BACKEND", backend):
            monitor, refusal, is_all = (
                monitors_mod.resolve_wallpaper_monitor("all"))
            self.assertTrue(is_all)
            self.assertIsNone(monitor)
            monitor, refusal, is_all = (
                monitors_mod.resolve_wallpaper_monitor(None))
            self.assertFalse(is_all)
            self.assertIsNone(monitor)

    def test_unknown_selector_refuses_listing_monitors(self):
        backend = _FakeBackend(_two_monitors())
        with mock.patch.object(monitors_mod, "_TABLE_BACKEND", backend):
            monitor, refusal, is_all = (
                monitors_mod.resolve_wallpaper_monitor("seventh monitor"))
            self.assertIsNone(monitor)
            self.assertIn("Unknown monitor", refusal)
            self.assertIn("DISPLAY1", refusal)
            self.assertIn("DISPLAY2", refusal)

    def test_per_monitor_unavailable_off_windows(self):
        # Real code path on Linux: no COM object can be created.
        self.assertFalse(monitors_mod.is_windows())
        self.assertFalse(monitors_mod.per_monitor_available())


class TestSetOneMonitor(unittest.TestCase):
    def test_set_one_monitor_leaves_others_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(
                images={DISPLAY1: old1, DISPLAY2: old2})
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com) as (_u32, _com):
                result = wallpaper_mod.apply_wallpaper(
                    new, monitor="second monitor")
            self.assertTrue(result["applied"], result)
            self.assertEqual(result["reason"], "ok")
            self.assertFalse(result["fell_back"])
            sets = [c for c in com.calls if c[0] == "set"]
            self.assertEqual(sets, [("set", DISPLAY2, new)])
            self.assertEqual(com.images[DISPLAY1], old1)
            self.assertEqual(com.images[DISPLAY2], new)
            self.assertEqual(_u32.calls, [])
            saved = _read_record(record)
            self.assertEqual(
                saved["monitors"][DISPLAY2]["image"], old2)
            self.assertEqual(
                saved["monitors"][DISPLAY2]["kind"], "image")
            self.assertNotIn(DISPLAY1, saved.get("monitors", {}))
            # The global record was NOT moved by a per-monitor set.
            self.assertNotIn("image", saved)

    def test_set_all_sets_each_monitor_individually(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(
                images={DISPLAY1: old1, DISPLAY2: old2})
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                result = wallpaper_mod.apply_wallpaper(new, monitor="all")
            self.assertTrue(result["applied"], result)
            sets = sorted(c for c in com.calls if c[0] == "set")
            self.assertEqual(sets, [("set", DISPLAY1, new),
                                    ("set", DISPLAY2, new)])
            saved = _read_record(record)
            self.assertEqual(
                saved["monitors"][DISPLAY1]["image"], old1)
            self.assertEqual(
                saved["monitors"][DISPLAY2]["image"], old2)

    def test_save_failure_blocks_per_monitor_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            store = {}
            _seed(store, "")
            com = _FakeDesktopWallpaper(images={DISPLAY1: None})
            # get_wallpaper returns None for DISPLAY2: unreadable.
            com.images.pop(DISPLAY2, None)
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                result = wallpaper_mod.apply_wallpaper(
                    new, monitor="second monitor")
            self.assertFalse(result["applied"], result)
            self.assertEqual(result["reason"], "could-not-read-current")
            self.assertEqual(
                [c for c in com.calls if c[0] == "set"], [])

    def test_read_back_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            other = _write_file(pathlib.Path(tmp) / "other.png", PNG_BYTES)
            store = {}
            _seed(store, old2)
            com = _FakeDesktopWallpaper(images={DISPLAY2: old2})
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                # The set "succeeds" but the monitor still shows
                # a different image afterwards.
                real_set = com.set_wallpaper

                def lying_set(monitor_id, image):
                    real_set(monitor_id, image)
                    com.images[monitor_id] = other
                    return True

                with mock.patch.object(com, "set_wallpaper",
                                       side_effect=lying_set):
                    result = wallpaper_mod.apply_wallpaper(
                        new, monitor="second monitor")
            self.assertFalse(result["applied"], result)
            self.assertEqual(result["reason"], "read-back-mismatch")

    def test_api_unavailable_falls_back_to_spi_and_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors())) as (_u32, _com):
                with mock.patch.object(
                        monitors_mod, "_get_desktop_wallpaper",
                        return_value=None):
                    result = wallpaper_mod.apply_wallpaper(
                        new, monitor="second monitor")
            self.assertTrue(result["applied"], result)
            self.assertEqual(
                result["reason"],
                "per-monitor-unavailable-fell-back-to-all")
            self.assertTrue(result["fell_back"])
            self.assertEqual(_u32.calls, [new])
            saved = _read_record(record)
            self.assertEqual(saved["image"], old1)

    def test_api_refusal_is_honest_with_no_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            store = {}
            _seed(store, old2)
            com = _FakeDesktopWallpaper(images={DISPLAY2: old2})
            com.refuse_set = True
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com) as (_u32, _com):
                result = wallpaper_mod.apply_wallpaper(
                    new, monitor="second monitor")
            self.assertFalse(result["applied"], result)
            self.assertEqual(result["reason"], "system-call-refused")
            self.assertEqual(_u32.calls, [])


class TestRevertOneMonitor(unittest.TestCase):
    def _seed_record(self, tmp, entries):
        path = pathlib.Path(tmp) / "wallpaper_previous.json"
        payload = {"version": 2, "image": "", "style": "10",
                   "tile": "0", "monitors": entries}
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_revert_one_monitor_restores_only_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            cur1 = _write_file(pathlib.Path(tmp) / "cur1.png", PNG_BYTES)
            cur2 = _write_file(pathlib.Path(tmp) / "cur2.png", PNG_BYTES)
            store = {}
            _seed(store, cur1)
            com = _FakeDesktopWallpaper(
                images={DISPLAY1: cur1, DISPLAY2: cur2})
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                self._seed_record(tmp, {
                    DISPLAY1: {"image": old1, "style": "10",
                               "tile": "0", "kind": "image",
                               "color": "", "slideshow": None},
                    DISPLAY2: {"image": old2, "style": "10",
                               "tile": "0", "kind": "image",
                               "color": "", "slideshow": None},
                })
                result = wallpaper_mod.revert_wallpaper(
                    monitor="primary")
            self.assertTrue(result["reverted"], result)
            self.assertEqual(result["image"], old1)
            self.assertEqual(com.images[DISPLAY1], old1)
            self.assertEqual(com.images[DISPLAY2], cur2)
            # The record is never cleared by a revert.
            saved = _read_record(record)
            self.assertEqual(saved["monitors"][DISPLAY1]["image"], old1)
            self.assertEqual(saved["monitors"][DISPLAY2]["image"], old2)
            # Reverting twice is idempotent per monitor.
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                again = wallpaper_mod.revert_wallpaper(
                    monitor="primary")
            self.assertTrue(again["reverted"], again)

    def test_vanished_monitor_does_not_break_the_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            cur1 = _write_file(pathlib.Path(tmp) / "cur1.png", PNG_BYTES)
            store = {}
            _seed(store, cur1)
            com = _FakeDesktopWallpaper(images={DISPLAY1: cur1})
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _one_monitor()), com=com):
                self._seed_record(tmp, {
                    DISPLAY1: {"image": old1, "style": "10",
                               "tile": "0", "kind": "image",
                               "color": "", "slideshow": None},
                    DISPLAY2: {"image": old2, "style": "10",
                               "tile": "0", "kind": "image",
                               "color": "", "slideshow": None},
                })
                result = wallpaper_mod.revert_wallpaper(monitor="all")
            self.assertFalse(result["reverted"], result)
            self.assertEqual(result["reason"], "partial")
            per = result["per_monitor"]
            self.assertTrue(per[DISPLAY1]["reverted"])
            self.assertEqual(per[DISPLAY2]["reason"], "monitor-gone")
            self.assertEqual(com.images[DISPLAY1], old1)
            # A single revert naming the vanished monitor refuses.
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _one_monitor()), com=com):
                single = wallpaper_mod.revert_wallpaper(
                    monitor="second monitor")
            self.assertFalse(single["reverted"], single)
            self.assertIn("unknown-monitor", single["reason"])

    def test_new_monitor_inherits_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(images={DISPLAY1: old1})
            table = _two_monitors() + [
                {"monitor_id": DISPLAY3, "device": DISPLAY3,
                 "primary": False, "left": 3840, "top": 0,
                 "width": 1920, "height": 1080, "scale": 1.0}]
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(table),
                                  com=com):
                self._seed_record(tmp, {
                    DISPLAY1: {"image": old1, "style": "10",
                               "tile": "0", "kind": "image",
                               "color": "", "slideshow": None},
                })
                result = wallpaper_mod.revert_wallpaper(
                    monitor="third monitor")
            self.assertFalse(result["reverted"], result)
            self.assertEqual(result["reason"],
                             "no-previous-saved-for-monitor")
            self.assertEqual(
                [c for c in com.calls if c[0] == "set"], [])

    def test_revert_unavailable_refuses_and_keeps_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors())) as (_u32, _com):
                self._seed_record(tmp, {
                    DISPLAY1: {"image": old1, "style": "10",
                               "tile": "0", "kind": "image",
                               "color": "", "slideshow": None},
                })
                with mock.patch.object(
                        monitors_mod, "_get_desktop_wallpaper",
                        return_value=None):
                    result = wallpaper_mod.revert_wallpaper(
                        monitor="primary")
            self.assertFalse(result["reverted"], result)
            self.assertEqual(result["reason"],
                             "per-monitor-unavailable")
            self.assertEqual(_u32.calls, [])
            saved = _read_record(record)
            self.assertEqual(saved["monitors"][DISPLAY1]["image"], old1)


class TestKindsRoundTrip(unittest.TestCase):
    def test_solid_colour_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = {}
            _seed(store, "")
            com = _FakeDesktopWallpaper(
                images={DISPLAY1: "", DISPLAY2: ""}, color="#112233")
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                saved = wallpaper_mod.save_previous_wallpaper_for(
                    DISPLAY2)
            self.assertTrue(saved["saved"], saved)
            entry = _read_record(record)["monitors"][DISPLAY2]
            self.assertEqual(entry["kind"], "solid")
            self.assertEqual(entry["image"], "")
            self.assertEqual(entry["color"], "#112233")
            # Revert: clears the wallpaper and restores the colour.
            com.images[DISPLAY2] = "x"
            com.color = "#000000"
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                result = wallpaper_mod.revert_wallpaper_for(DISPLAY2)
            self.assertTrue(result["reverted"], result)
            self.assertEqual(com.images[DISPLAY2], "")
            self.assertEqual(com.color, "#112233")

    def test_slideshow_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = _write_file(pathlib.Path(tmp) / "frame.png",
                                PNG_BYTES)
            store = {}
            _seed(store, frame)
            com = _FakeDesktopWallpaper(
                images={DISPLAY2: frame},
                slideshow={"active": True, "root": r"C:\Pics",
                           "interval_ms": 60000, "shuffle": True})
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com), \
                    mock.patch.object(
                        monitors_mod, "_read_slideshow_registry",
                        return_value={"root": r"C:\Pics",
                                      "interval_ms": 60000,
                                      "shuffle": True}):
                saved = wallpaper_mod.save_previous_wallpaper_for(
                    DISPLAY2)
            self.assertTrue(saved["saved"], saved)
            entry = _read_record(record)["monitors"][DISPLAY2]
            self.assertEqual(entry["kind"], "slideshow")
            self.assertEqual(entry["image"], frame)
            self.assertEqual(entry["slideshow"]["root"], r"C:\Pics")
            self.assertTrue(entry["slideshow"]["shuffle"])
            # Revert: the engine state is restored AND the frame
            # is set — and the frame having advanced since does
            # NOT make the read-back refuse.
            com.images[DISPLAY2] = "advanced-frame.png"
            com.slideshow["active"] = False
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com), \
                    mock.patch.object(
                        monitors_mod, "_read_slideshow_registry",
                        return_value={"root": r"C:\Pics",
                                      "interval_ms": 60000,
                                      "shuffle": True}), \
                    mock.patch.object(
                        monitors_mod, "_write_slideshow_registry",
                        return_value=True) as write_reg:
                result = wallpaper_mod.revert_wallpaper_for(DISPLAY2)
            self.assertTrue(result["reverted"], result)
            self.assertTrue(com.slideshow["active"])
            self.assertEqual(write_reg.call_count, 1)
            self.assertIn(("set", DISPLAY2, frame), com.calls)


class TestSingleMonitorUnchanged(unittest.TestCase):
    def test_legacy_path_is_spi_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(images={DISPLAY1: old1})
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _one_monitor()), com=com) as (_u32, _com):
                result = wallpaper_mod.apply_wallpaper(new)
            self.assertTrue(result["applied"], result)
            self.assertEqual(result["reason"], "ok")
            self.assertEqual(_u32.calls, [new])
            self.assertEqual(
                [c for c in com.calls if c[0] == "set"], [])
            saved = _read_record(record)
            self.assertEqual(saved["image"], old1)
            self.assertEqual(saved["version"], 2)
            self.assertNotIn("monitors", saved)
            # And the legacy revert restores via SPI only.
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _one_monitor()), com=com) as (_u32b, _comb):
                reverted = wallpaper_mod.revert_wallpaper()
            self.assertTrue(reverted["reverted"], reverted)
            self.assertEqual(_u32b.calls, [old1])
            self.assertEqual(
                [c for c in _comb.calls if c[0] == "set"], [])

    def test_primary_on_one_monitor_matches_spi_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(images={DISPLAY1: old1})
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _one_monitor()), com=com):
                result = wallpaper_mod.apply_wallpaper(
                    new, monitor="primary")
            self.assertTrue(result["applied"], result)
            self.assertEqual(com.images[DISPLAY1], new)


class TestRecordCompatibility(unittest.TestCase):
    def test_v1_record_still_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = _write_file(pathlib.Path(tmp) / "old.png", PNG_BYTES)
            store = {}
            _seed(store, old)
            path = pathlib.Path(tmp) / "wallpaper_previous.json"
            path.write_text(json.dumps(
                {"version": 1, "image": old, "style": "6",
                 "tile": "0"}), encoding="utf-8")
            with _record_path(tmp), _fake_windows(store):
                loaded = wallpaper_mod.load_previous_wallpaper()
            self.assertEqual(loaded, {"image": old, "style": "6",
                                      "tile": "0"})
            self.assertIsNone(
                wallpaper_mod.load_previous_wallpaper_for(DISPLAY1))

    def test_global_save_preserves_monitor_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(images={DISPLAY2: old2})
            with _record_path(tmp) as record, \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper_for(
                        DISPLAY2)["saved"])
                self.assertTrue(
                    wallpaper_mod.save_previous_wallpaper()["saved"])
            saved = _read_record(record)
            self.assertEqual(saved["image"], old1)
            self.assertEqual(
                saved["monitors"][DISPLAY2]["image"], old2)


class TestOtherSetPaths(unittest.TestCase):
    def test_set_wallpaper_tool_monitor_param(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(
                images={DISPLAY1: old1, DISPLAY2: old2})
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com), \
                    mock.patch.object(prefs_mod, "_is_windows",
                                      return_value=True):
                result = prefs_mod.set_wallpaper(
                    new, style="fill", monitor="primary")
            self.assertIn("Wallpaper set to", result, result)
            self.assertIn("primary", result, result)
            self.assertEqual(
                [c for c in com.calls if c[0] == "set"],
                [("set", DISPLAY1, new)])
            self.assertEqual(com.images[DISPLAY2], old2)
            # Style is global: written for the whole desktop.
            self.assertEqual(
                store[(_DESKTOP, "WallpaperStyle")], "10")

    def test_set_wallpaper_tool_without_monitor_is_spi(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            store = {}
            _seed(store, old1)
            com = _FakeDesktopWallpaper(images={DISPLAY1: old1})
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com) as (_u32, _com), \
                    mock.patch.object(prefs_mod, "_is_windows",
                                      return_value=True):
                result = prefs_mod.set_wallpaper(new, style="fill")
            self.assertIn("Wallpaper set to", result, result)
            self.assertEqual(_u32.calls, [new])
            self.assertEqual(
                [c for c in com.calls if c[0] == "set"], [])

    def test_display_control_monitor_param(self):
        with tempfile.TemporaryDirectory() as tmp:
            new = _write_file(pathlib.Path(tmp) / "new.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            store = {}
            _seed(store, old2)
            com = _FakeDesktopWallpaper(images={DISPLAY2: old2})
            from core.desktop import control_plus_desktop as _cpd

            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com), \
                    mock.patch.object(_cpd, "_is_windows",
                                      return_value=True):
                result = display_control(
                    "set_wallpaper", path=new, monitor="right")
            self.assertIn("Wallpaper set to", str(result), str(result))
            self.assertEqual(
                [c for c in com.calls if c[0] == "set"],
                [("set", DISPLAY2, new)])


class TestFunnelAndSchema(FunnelBase):
    def test_revert_monitor_param_reports_the_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            cur1 = _write_file(pathlib.Path(tmp) / "cur1.png", PNG_BYTES)
            store = {}
            _seed(store, cur1)
            com = _FakeDesktopWallpaper(images={DISPLAY1: cur1})
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _two_monitors()), com=com):
                path = pathlib.Path(tmp) / "wallpaper_previous.json"
                path.write_text(json.dumps(
                    {"version": 2, "image": "", "style": "10",
                     "tile": "0", "monitors": {
                        DISPLAY1: {"image": old1, "style": "10",
                                   "tile": "0", "kind": "image",
                                   "color": "", "slideshow": None}}}),
                    encoding="utf-8")
                result = self.call(
                    "wallpaper_revert", {"monitor": "primary"},
                    approve=True)
            self.assertEqual(result["status"], "success", result)
            self.assertIn("Wallpaper reverted to", result["result"])
            self.assertIn(old1, result["result"])
            self.assertIn(DISPLAY1, result["result"])

    def test_revert_all_partial_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            old1 = _write_file(pathlib.Path(tmp) / "old1.png", PNG_BYTES)
            old2 = _write_file(pathlib.Path(tmp) / "old2.png", PNG_BYTES)
            cur1 = _write_file(pathlib.Path(tmp) / "cur1.png", PNG_BYTES)
            store = {}
            _seed(store, cur1)
            com = _FakeDesktopWallpaper(images={DISPLAY1: cur1})
            with _record_path(tmp), \
                    _fake_windows(store, backend=_FakeBackend(
                        _one_monitor()), com=com):
                path = pathlib.Path(tmp) / "wallpaper_previous.json"
                path.write_text(json.dumps(
                    {"version": 2, "image": "", "style": "10",
                     "tile": "0", "monitors": {
                        DISPLAY1: {"image": old1, "style": "10",
                                   "tile": "0", "kind": "image",
                                   "color": "", "slideshow": None},
                        DISPLAY2: {"image": old2, "style": "10",
                                   "tile": "0", "kind": "image",
                                   "color": "", "slideshow": None}}}),
                    encoding="utf-8")
                result = self.call(
                    "wallpaper_revert", {"monitor": "all"},
                    approve=True)
            self.assertEqual(result["status"], "success", result)
            self.assertIn("some screens, not all", result["result"])
            self.assertIn("monitor-gone", result["result"])

    def test_monitor_properties_and_registry_count(self):
        decls = {d["name"]: d for d in tools.get_declarations()}
        self.assertEqual(len(decls), 764)  # INTENTIONAL — 673 -> 677: score_intervention, run_proactivity_bench, list_proactivity_scores, proactivity_bench_results (R7-8 proactive-agent, 2026-11-10).; R8-1 fastembed port: 677 -> 680 (+3, not recorded in this file); R8-3 sumy port (2026-11-10): 680 -> 681 — summarise_text; R8-4 screen-break port (2026-11-10): 681 -> 684 — eye_rest_timer, breathing_guide, log_glass added; R8-5 magic-wormhole port (2026-11-10): 684 -> 687 — wormhole_offer, wormhole_receive, wormhole_status added; R8-6 yasb port (2026-11-10): 687 -> 688 — status_strip added; R8-7 basic-pitch port (2026-11-10): 688 -> 690 — audio_to_midi, audio_to_midi_status added; R8-8 mftool port (2026-11-10): 690 -> 692 — fund_returns, mutual_fund_nav added; R8-9 pypresence adoption (2026-11-10): 692 -> 694 — discord_presence, set_discord_client_id added; R9-2 rustic adoption (2026-11-10): 694 -> 699 — init_backup_repo, backup_files, list_backup_snapshots, restore_backup, backup_status added; R9-3 whoosh port (2026-11-10): 699 -> 702 — index_files, search_indexed_files, disk_index_status added; R9-4 mutagen port (2026-11-10): 702 -> 704 — sync_folders_two_way, two_way_sync_status added; R9-8 elodie port (2026-11-10): 704 -> 706 — organise_photos, preview_photo_organisation added; R10-1 chrome-devtools-mcp port (2026-11-10): 706 -> 709 — browser_console, browser_network, browser_performance added; R10-3 browser-history port (2026-11-10): 709 -> 711 — browser_history, browser_bookmarks added; R10-4 python-adblock port (2026-11-10): 711 -> 713 — check_url_blocked, filter_list_status added; R10-5 python-readability port (2026-11-10): 713 -> 714 — extract_article added; R11-1 ollama port (2026-11-10): 714 -> 717 — ollama_status + ollama_pull + ollama_delete added; R16-8 event-planning pack (2026-11-11): 717 -> 723 — event_plan + event_guest + event_vendor + event_budget + event_milestone + event_schedule added; R16-9 attic pack (2026-11-11): 723 -> 724 — home_inventory added; R16-10 GreaterWMS pack (2026-11-11): 724 -> 725 — warehouse_stock added; R17-1 bookrail pack (2026-11-11): 725 -> 726 — bookings added; R17-2 trivy pack (2026-11-11): 726 -> 727 — security_scan added (read-only); R17-3 go2rtc pack (2026-11-11): 727 -> 728 — ip_camera added (effectful, capture); R17-4 MCSManager pack (2026-11-11): 728 -> 729 — game_server added (effectful, confirm-gated); R17-5 komga pack (2026-11-11): 729 -> 730 — ebook_library added (effectful, free by design); R17-6 openstays pack (2026-11-11): 730 -> 731 — lodging added (effectful, free by design); R17-7 qlcplus pack (2026-11-12): 731 -> 732 — stage_lighting added (effectful, confirm-gated); R17-8 pvlib pack (2026-11-12): 732 -> 733 — solar_yield added (read-only); R17-9 wardrobe pack (2026-11-12): 733 -> 734 — wardrobe added (effectful, free by design); R18-1 stripe pack (2026-11-12): 734 -> 735 — billing added (effectful, confirm-gated); R18-3 mcp-context-forge pack (2026-11-12): 735 -> 736; R18-4 a2a pack (2026-11-12): 736 -> 737; R18-5 dspy pack (2026-11-12): 737 -> 738; R18-6 fonoster pack (2026-11-12): 738 -> 739; R18-7 attendize pack (2026-11-12): 739 -> 740; R18-8 ta pack (2026-11-12): 740 -> 741; R18-10 timetrace pack (2026-11-12): 741 -> 742; R19-1 videolingo pack (2026-11-12): 742 -> 743; R19-2 ortools pack (2026-11-12): 743 -> 744; R19-3 aureuserp pack (2026-11-12): 744 -> 745; R19-4 vidbee pack (2026-11-12): 745 -> 746; R19-5 invoify pack (2026-11-12): 746 -> 747; R19-6 tolgee pack (2026-11-12): 747 -> 748; R19-7 spoolman pack (2026-11-12): 748 -> 749; R19-8 djangocrm pack (2026-11-12): 749 -> 750; R19-10 vroom pack (2026-11-12): 750 -> 751; GAPS-7 connectors pack (2026-11-13): 751 -> 752; SOCIAL Phase 1 (2026-11-13): 752 -> 753; GAPS-8 scheduled-agents pack (2026-11-13): 753 -> 754; Click-anywhere (2026-11-14): 754 -> 755 — one new tool click_anywhere; field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine; round-3 fix #4 (2026-11-19): 756 -> 757 — one new tool run_plan; round-3 fix Q6 (2026-11-19): 757 -> 758 — one new tool get_morning_briefing; Stage 0 features batch 2 (2026-11-22): census 758/2079/383/375/167 -> 762/2095/384/378/169; Stage 0 features batch 3 (2026-11-22): census 762/2095/384/378/169 -> 764/2102/384/380/169
        for name in ("wallpaper_apply", "wallpaper_revert",
                     "set_wallpaper", "display_control"):
            props = (decls[name].get("parameters") or {}).get(
                "properties") or {}
            self.assertIn("monitor", props, name)
            spec = props["monitor"]
            self.assertEqual(spec.get("type"), "string", name)
            self.assertIn("description", spec, name)
        self.assertIn("wallpaper_revert",
                      gate_confirm._CONFIRM_GATE_TOOLS)
        self.assertTrue(
            tools_mod._needs_confirmation("wallpaper_revert", {}))
        self.assertNotIn("wallpaper_revert",
                         gate_readonly._READ_ONLY_TOOLS)


if __name__ == "__main__":
    unittest.main()
