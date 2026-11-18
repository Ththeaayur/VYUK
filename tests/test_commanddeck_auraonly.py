"""Aura-only orb consolidation (owner decision 2026-11-01, "keep only
the aura"): Aura is the single orb style. Every other style is
deleted, saved non-Aura values migrate to Aura on load, and the
style picker is gone (a static Aura label remains in Settings).
"""

import sys
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
try:
    from PySide6.QtWidgets import QApplication
    _QT_IMPORTABLE = True
except Exception:
    _QT_IMPORTABLE = False


# T-3 (debug pass): gui modules import the frozen voice_assistant.*
# package names (gui/mini_orb.py does `from voice_assistant.core.
# gui_facade import ...`) — the alias is registered at runtime by
# doctor._register_package(), so tests register it themselves.
# Snapshot/restore discipline (L3): register only keys that are
# absent, and tearDownModule removes exactly the keys this module
# added — never delete-all.
_ADDED_VA_MODULES = []


def setUpModule():
    for key, path in (
        ("voice_assistant", str(TREE)),
        ("voice_assistant.core", str(TREE / "core")),
        ("voice_assistant.gui", str(TREE / "gui")),
    ):
        if key not in sys.modules:
            pkg = types.ModuleType(key)
            pkg.__path__ = [path]
            sys.modules[key] = pkg
            _ADDED_VA_MODULES.append(key)


def tearDownModule():
    for key in _ADDED_VA_MODULES:
        sys.modules.pop(key, None)
    _ADDED_VA_MODULES.clear()


def _qt_available():
    if not _QT_IMPORTABLE:
        return False
    try:
        if QApplication.instance() is None:
            QApplication([])
        return True
    except Exception:
        return False


QT_AVAILABLE = _qt_available()


def _src(rel):
    return (TREE / rel).read_text(encoding="utf-8")


# INTENTIONAL (refactor P1 step 5): window_settings.py split verbatim
# into family modules; the "no style picker" negative pins must hold
# across the whole settings surface, so read it concatenated.
SETTINGS_FILES = (
    "gui/window_settings.py",
    "gui/window_settings_think.py",
    "gui/window_settings_voice.py",
    "gui/window_settings_groups.py",
    "gui/window_settings_advbrain.py",
    "gui/window_settings_advcards.py",
    "gui/window_settings_appearance.py",
    "gui/window_settings_privacy.py",
)


def _settings_source():
    return "".join(_src(n) for n in SETTINGS_FILES)


class AuraOnlyRosterTests(unittest.TestCase):
    def test_orb_styles_is_aura_only(self):
        from core.app_settings import ORB_STYLES
        self.assertEqual(ORB_STYLES, ("Aura",))

    def test_migrate_orb_style(self):
        from core.app_settings import migrate_orb_style
        self.assertEqual(migrate_orb_style("Aura"), "Aura")
        for dead in ("Apex", "Reactor", "Sphere", "Ring", "Wave",
                     "Prism", "Halo", "Eclipse", "Nope", "", None):
            self.assertEqual(migrate_orb_style(dead), "Aura", dead)

    def test_schema_default_is_aura(self):
        from core.settings_schema import NovaSettingsData
        self.assertEqual(NovaSettingsData().orb_style, "Aura")

    def test_no_style_picker_in_settings(self):
        # INTENTIONAL (refactor P1 step 5): see _settings_source above.
        settings = _settings_source()
        self.assertNotIn("style_buttons", settings)
        self.assertNotIn("_on_orb_style_selected", settings)
        self.assertIn('QLabel("Orb: Aura")', settings)

    def test_no_cycle_action(self):
        self.assertNotIn("_cycle_orb_style", _src("gui/window_chat.py"))
        self.assertNotIn("switch_orb_style", _src("gui/window_widgets.py"))
        from core.command_palette import SAFE_ACTION_IDS
        self.assertNotIn("switch_orb_style", SAFE_ACTION_IDS)

    def test_no_vitals_entry_point(self):
        self.assertNotIn("set_vitals", _src("gui/visualizer.py"))
        self.assertNotIn("set_vitals", _src("gui/window_services.py"))

    def test_no_deleted_style_branches(self):
        # INTENTIONAL (GUI split #7): _paint_aura moved verbatim to
        # visualizer_paint.py; the dead-style check follows it.
        viz = _src("gui/visualizer_paint.py")
        for dead in ("_paint_apex", "_paint_reactor", "_paint_prism",
                     "_paint_halo", "_paint_eclipse", "_paint_sphere",
                     "_paint_ring", "_paint_wave", "_paint_core"):
            self.assertNotIn(dead, viz, dead)
        self.assertIn("def _paint_aura", viz)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 + QApplication required")
class AuraOnlyRuntimeTests(unittest.TestCase):
    def test_spring_step_shared_helper_survives(self):
        # Regression pin (2026-11-01): _spring_step sat between two
        # Apex methods and was swallowed by an early deletion span;
        # it is shared Aura infrastructure via _spring_step_dt.
        from gui.visualizer import OrbVisualizer
        self.assertTrue(callable(OrbVisualizer._spring_step))
        val, vel = OrbVisualizer._spring_step(0.0, 0.0, 1.0, 0.2, 0.8)
        self.assertGreater(val, 0.0)

    def test_visualizer_normalises_to_aura(self):
        from gui.visualizer import OrbVisualizer
        orb = OrbVisualizer()
        self.assertEqual(orb.orb_style, "Aura")
        for dead in ("Apex", "Reactor", "Nope"):
            orb.set_orb_style(dead)
            self.assertEqual(orb.orb_style, "Aura", dead)

    def test_mini_canvas_normalises_to_aura(self):
        from gui.mini_orb import _AuraMiniCanvas
        canvas = _AuraMiniCanvas()
        self.assertEqual(canvas.orb_style, "Aura")
        canvas.set_orb_style("Apex")
        self.assertEqual(canvas.orb_style, "Aura")
        self.assertEqual(canvas.canvas_family(), "aura")

    def test_thumbnail_default_aura_and_apex_raises(self):
        from gui.design_system.orbthumb import OrbThumbnail
        self.assertEqual(OrbThumbnail()._style, "Aura")
        with self.assertRaises(ValueError):
            OrbThumbnail(style="Apex")


if __name__ == "__main__":
    unittest.main()
