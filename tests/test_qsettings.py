"""GAPS-11: the QSettings migration (flaw #2, second half).

Before this batch the settings were read through QSettings
("NovaAI", "Nova") scattered across 32 files — 158 literal-key
.value()/.setValue() calls, 54 distinct keys, 14 constructions —
with no spec home: the settings_schema closing step (app_settings
reading QSettings directly via its own _qs_get/_qs_set) had never
landed. Now `core/config_spec.py` is the single home: the
QSETTINGS_FIELDS table (96 entries) says what each key means, its
type, and its default, and every product-code read/write goes
through the five helpers (qsettings_store / _get / _set /
_contains / _remove). Behaviour is preserved: every default in
the table is the default the call site used, proven in
prove_qsettings.py against the pre-migration tree.

The two dead alias legs (VYUK_ALLOW_REAL_CHROME_ATTACH,
VYUK_BROWSER_DEBUG_PROFILE_DIR) were REMOVED, not revived: in
production _config_value always returns the config field's
non-None value, so the alias only fired when the config module
was stubbed, and reviving it would let a legacy env var enable
real-Chrome attach despite the field being False. The canonical
env vars (BROWSER_ALLOW_REAL_CHROME_ATTACH,
BROWSER_DEBUG_PROFILE_DIR) already control the fields.
"""

import os
import pathlib
import sys
import tempfile
import types
import unittest

from core import config_spec as cs
from core.settings_schema import (FIELD_SPECS, SettingsStore,
                                  apply_to_store, model_from_store)

TREE = pathlib.Path(__file__).resolve().parent.parent

try:
    from PySide6.QtCore import QSettings  # noqa: F401
    HAS_QT = True
except ImportError:
    HAS_QT = False


class _FakeStore:
    """Dict-backed QSettings stand-in (value/setValue/contains/
    remove/sync) — the no-Qt stand-in for the helpers."""

    def __init__(self, values=None):
        self.data = dict(values or {})

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


def _register_packages():
    """Mirror test_wiring_triage_v600._register_packages: make
    voice_assistant.* importable from this folder whatever it is
    called, so the lazy w9_wiring imports resolve."""
    if "voice_assistant" not in sys.modules:
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(TREE)]
        sys.modules["voice_assistant"] = pkg
    if "voice_assistant.core" not in sys.modules:
        core = types.ModuleType("voice_assistant.core")
        core.__path__ = [str(TREE / "core")]
        sys.modules["voice_assistant.core"] = core


_register_packages()


def _need_w9(test):
    try:
        from core import w9_wiring_chat  # noqa: F401
    except Exception as exc:  # noqa: BLE001 — missing dep: skip
        test.skipTest("w9 import chain unavailable: %s" % exc)


class QSettingsTableTests(unittest.TestCase):
    def test_table_has_108_entries(self):
        # Chat UI build (2026-11-16): 90 + chat/composer_draft +
        # chat/last_seen_id. Field-check fix #7 (U4 #31):
        # + chat/last_seen_store_id. Field-check fix #12
        # (M11a): +2 (audio/output_device[_name]).
        # Rethink Build 5 (§9.1): +1 (browser/connect_offer_shown).
        # Round-3 wizard expansion (2026-11-21): +1
        # (firstrun/selftest_faults — the self-test fix-it pin).
        # Loophole batch 7 (2026-11-22): +2
        # (firstrun/pieces_declined +
        # firstrun/setup_card_snoozed_until — the per-piece
        # setup card's declines and snooze).
        # Stage 0 features batch 6 (2026-11-23): +1
        # (quiet/calendar_quiet_enabled — calendar-aware quiet).
        # UI Phase 1 (2026-11-23): +3 (home/city, appearance/
        # hud_profile, learning/reviewed_ids).
        # UI Phase 2 (2026-11-25): +2 (skills/pinned, palette/recent).
        # UI Phase 3 (2026-11-25): +1 (calendar/view).
        # UI Phase 4 (2026-11-26): +2 (pulse/refresh_ms,
        # doctor/run_selection).
        self.assertEqual(len(cs.QSETTINGS_FIELDS), 108)

    def test_names_and_keys_unique(self):
        names = [n for n, _k, _t, _d in cs.QSETTINGS_FIELDS]
        keys = [k for _n, k, _t, _d in cs.QSETTINGS_FIELDS]
        # Stage 0 features batch 6 (2026-11-23): 99 -> 100
        # (quiet/calendar_quiet_enabled).
        # UI upgrade Phase 1 (2026-11-24): 100 -> 103
        # (home/city, appearance/hud_profile, learning/reviewed_ids).
        # UI Phase 2 (2026-11-25): 103 -> 105
        # (skills/pinned, palette/recent).
        # UI Phase 3 (2026-11-25): 105 -> 106 (calendar/view).
        # UI Phase 4 (2026-11-26): 106 -> 108 (pulse/refresh_ms,
        # doctor/run_selection).
        self.assertEqual(len(set(names)), 108)
        self.assertEqual(len(set(keys)), 108)

    def test_types_valid(self):
        for _n, _k, typ, _d in cs.QSETTINGS_FIELDS:
            self.assertIn(typ, (str, bool, int, float, list, object))

    def test_defaults_cover_all_keys(self):
        defaults = cs.qsettings_defaults()
        # Stage 0 features batch 6 (2026-11-23): 99 -> 100
        # (quiet/calendar_quiet_enabled).
        # UI upgrade Phase 1 (2026-11-24): 100 -> 103
        # (home/city, appearance/hud_profile, learning/reviewed_ids).
        # UI Phase 2 (2026-11-25): 103 -> 105
        # (skills/pinned, palette/recent).
        # UI Phase 3 (2026-11-25): 105 -> 106 (calendar/view).
        # UI Phase 4 (2026-11-26): 106 -> 108 (pulse/refresh_ms,
        # doctor/run_selection).
        self.assertEqual(len(defaults), 108)
        table = {k: d for _n, k, _t, d in cs.QSETTINGS_FIELDS}
        self.assertEqual(defaults, table)

    def test_field_specs_agreement(self):
        """The 6 FIELD_SPECS QSettings-backed fields agree with
        the spec table on key and default."""
        table = {n: (k, d) for n, k, _t, d in cs.QSETTINGS_FIELDS}
        shared = {n: (k, d) for n, (_label, d, _kind, k, source)
                  in FIELD_SPECS.items() if source == "qsettings"}
        self.assertEqual(len(shared), 6)
        for name, (key, default) in shared.items():
            self.assertIn(name, table)
            self.assertEqual(table[name], (key, default))

    def test_spot_defaults(self):
        defaults = cs.qsettings_defaults()
        self.assertEqual(defaults["theme"], "Cyan")
        self.assertEqual(defaults["orb_style"], "Aura")
        self.assertEqual(defaults["motion/level"], "full")
        self.assertEqual(defaults["audio/input_mode"], "auto")
        self.assertEqual(defaults["appearance/custom_hex"], "#35D6FF")
        self.assertEqual(defaults["presence/briefing_time"], "07:30")
        self.assertEqual(defaults["sounds_volume"], 0.5)
        self.assertEqual(defaults["learning/seen_ids"], [])


class HelperTests(unittest.TestCase):
    def test_round_trip_fake_store(self):
        store = _FakeStore()
        cs.qsettings_set("theme", "Cobalt", settings=store)
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Cobalt")
        self.assertTrue(cs.qsettings_contains("theme", settings=store))
        cs.qsettings_remove("theme", settings=store)
        self.assertFalse(cs.qsettings_contains("theme", settings=store))
        # A removed key reads back as the table default.
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Cyan")

    def test_unknown_key_raises(self):
        store = _FakeStore()
        for fn in (cs.qsettings_get, cs.qsettings_contains,
                   cs.qsettings_remove):
            with self.assertRaises(KeyError, msg=fn.__name__):
                fn("no/such/key", settings=store)
        with self.assertRaises(KeyError):
            cs.qsettings_set("no/such/key", "x", settings=store)

    def test_bool_coercion(self):
        store = _FakeStore({"sounds_enabled": "true"})
        self.assertTrue(cs.qsettings_get("sounds_enabled",
                                         settings=store))
        store.setValue("sounds_enabled", "0")
        self.assertFalse(cs.qsettings_get("sounds_enabled",
                                          settings=store))
        store.setValue("sounds_enabled", "garbage")
        self.assertTrue(cs.qsettings_get("sounds_enabled",
                                         settings=store))

    def test_int_float_coercion(self):
        store = _FakeStore({"audio/input_device": "3",
                            "sounds_volume": "0.75"})
        self.assertEqual(cs.qsettings_get("audio/input_device",
                                          settings=store), 3)
        self.assertEqual(cs.qsettings_get("sounds_volume",
                                          settings=store), 0.75)
        store.setValue("audio/input_device", "not-a-number")
        self.assertIsNone(cs.qsettings_get("audio/input_device",
                                           settings=store))
        store.setValue("sounds_volume", "not-a-number")
        self.assertEqual(cs.qsettings_get("sounds_volume",
                                          settings=store), 0.5)

    def test_set_coerces_and_rejects(self):
        store = _FakeStore()
        cs.qsettings_set("sounds_volume", "0.25", settings=store)
        self.assertEqual(store.data["sounds_volume"], 0.25)
        with self.assertRaises(ValueError):
            cs.qsettings_set("sounds_volume", "not-a-number",
                             settings=store)

    def test_set_none_removes(self):
        store = _FakeStore({"theme": "Cobalt"})
        cs.qsettings_set("theme", None, settings=store)
        self.assertFalse(cs.qsettings_contains("theme", settings=store))
        self.assertEqual(cs.qsettings_get("theme", settings=store),
                         "Cyan")

    def test_list_object_passthrough(self):
        store = _FakeStore({"learning/seen_ids": ["a", "b"]})
        self.assertEqual(cs.qsettings_get("learning/seen_ids",
                                          settings=store), ["a", "b"])
        self.assertEqual(cs.qsettings_get("geometry", settings=store),
                         None)

    def test_onboarding_sounds_probe(self):
        """The onboarding unset probe: a fresh store has never
        set sounds_enabled, and the probe must be able to tell
        that apart from an explicit False (fresh installs start
        silent until the permissions stage opts in)."""
        store = _FakeStore()
        self.assertFalse(cs.qsettings_contains("sounds_enabled",
                                               settings=store))
        self.assertTrue(cs.qsettings_get("sounds_enabled",
                                         settings=store))
        cs.qsettings_set("sounds_enabled", False, settings=store)
        self.assertTrue(cs.qsettings_contains("sounds_enabled",
                                              settings=store))
        self.assertFalse(cs.qsettings_get("sounds_enabled",
                                          settings=store))


class NoQtDegradationTests(unittest.TestCase):
    @unittest.skipIf(HAS_QT, "no-Qt degradation only without PySide6")
    def test_get_returns_default_without_qt(self):
        self.assertEqual(cs.qsettings_get("theme"), "Cyan")

    @unittest.skipIf(HAS_QT, "no-Qt degradation only without PySide6")
    def test_contains_false_without_qt(self):
        self.assertFalse(cs.qsettings_contains("theme"))

    @unittest.skipIf(HAS_QT, "no-Qt degradation only without PySide6")
    def test_set_remove_raise_without_qt(self):
        with self.assertRaises(RuntimeError):
            cs.qsettings_set("theme", "Cobalt")
        with self.assertRaises(RuntimeError):
            cs.qsettings_remove("theme")

    @unittest.skipIf(not HAS_QT, "production store needs PySide6")
    def test_production_store_is_nova(self):
        store = cs.qsettings_store()
        self.assertEqual(store.organizationName(), "NovaAI")
        self.assertEqual(store.applicationName(), "Nova")


class RealQSettingsTests(unittest.TestCase):
    @unittest.skipIf(not HAS_QT, "real QSettings needs PySide6")
    def test_ini_backed_round_trip(self):
        from PySide6.QtCore import QSettings as _QS
        with tempfile.TemporaryDirectory() as tmp:
            ini = os.path.join(tmp, "test.ini")
            real = _QS(ini, _QS.Format.IniFormat)
            cs.qsettings_set("theme", "Cobalt", settings=real)
            cs.qsettings_set("sounds_volume", 0.25, settings=real)
            self.assertEqual(cs.qsettings_get("theme", settings=real),
                             "Cobalt")
            self.assertEqual(cs.qsettings_get("sounds_volume",
                                              settings=real), 0.25)
            # A fresh read of the same file sees the values.
            reopened = _QS(ini, _QS.Format.IniFormat)
            self.assertEqual(cs.qsettings_get("theme",
                                              settings=reopened),
                             "Cobalt")
            self.assertEqual(cs.qsettings_get("sounds_volume",
                                              settings=reopened), 0.25)


class SettingsStoreTests(unittest.TestCase):
    def test_store_reads_writes_through_helpers(self):
        store = SettingsStore(_FakeStore())
        self.assertEqual(store.read("theme"), "Cyan")
        store.write("theme", "Cobalt")
        self.assertEqual(store.read("theme"), "Cobalt")
        store.reset("theme")
        self.assertEqual(store.read("theme"), "Cyan")

    def test_model_round_trip(self):
        store = SettingsStore(_FakeStore())
        model = model_from_store(store)
        model.theme = "Cobalt"
        apply_to_store(store, model)
        self.assertEqual(store.read("theme"), "Cobalt")


class W9MigrationTests(unittest.TestCase):
    def test_migrate_and_reset(self):
        _need_w9(self)
        from core import w9_wiring_chat as w9
        fake = _FakeStore({"appearance/density": "compact"})
        store = w9.settings_store(fake)
        migrated = w9.migrate_settings_store(store)
        self.assertEqual(migrated, ["density"])
        self.assertEqual(cs.qsettings_get("density", settings=fake),
                         "compact")
        # Already migrated: never overwritten, nothing to do.
        self.assertEqual(w9.migrate_settings_store(store), [])
        w9.reset_settings_row(store, "density")
        self.assertFalse(cs.qsettings_contains("density", settings=fake))
        self.assertFalse(cs.qsettings_contains("appearance/density",
                                               settings=fake))


class AliasLegRemovalTests(unittest.TestCase):
    def test_alias_legs_not_read(self):
        cdp = (TREE / "core" / "desktop" / "browser_cdp.py").read_text(
            encoding="utf-8")
        self.assertNotIn('explicit("VYUK_ALLOW_REAL_CHROME_ATTACH")',
                         cdp)
        self.assertNotIn(
            'explicit("VYUK_BROWSER_DEBUG_PROFILE_DIR")', cdp)
        self.assertNotIn("explicit(_REAL_ATTACH_ENV)", cdp)
        self.assertNotIn("explicit(_DEBUG_PROFILE_ENV)", cdp)
        self.assertNotIn("_REAL_ATTACH_ENV", cdp)
        self.assertNotIn("_DEBUG_PROFILE_ENV", cdp)
        self.assertIn("BROWSER_ALLOW_REAL_CHROME_ATTACH=1", cdp)
        self.assertIn("BROWSER_DEBUG_PROFILE_DIR) to a", cdp)
        self.assertNotIn("VYUK_BROWSER_DEBUG_PROFILE_DIR) to a", cdp)

    def test_aliases_survive_only_in_spec_env_alias(self):
        self.assertIn("VYUK_ALLOW_REAL_CHROME_ATTACH", cs.ENV_ALIAS)
        self.assertIn("VYUK_BROWSER_DEBUG_PROFILE_DIR", cs.ENV_ALIAS)

    def test_no_qsettings_construction_outside_spec(self):
        import ast
        hits = []
        for sub in ("core", "gui"):
            base = TREE / sub
            for path in base.rglob("*.py"):
                if "__pycache__" in str(path):
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8",
                                                errors="replace"))
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "QSettings"):
                        hits.append(str(path.relative_to(TREE)))
        self.assertEqual(hits, ["core/config_spec.py"])


if __name__ == "__main__":
    unittest.main()
