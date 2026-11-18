"""Libwiring sweep: the Phosphor Duotone icon bundling step, landed.

B-R1-6 (2026-11-09): Phosphor Duotone REPLACED Lucide as the
ONE primary icon system (owner-picked round-1 best bet). The
Lucide bundle (landed 2026-11-02, 15 SVGs, ISC) is removed;
this suite proves the landed Phosphor bundle
(gui/assets/icons/phosphor/):

- the shipped manifest is well-formed (19 Phosphor Duotone
  entries, zero validation problems, every path an existing
  file, every SVG valid XML with a 256x256 viewBox, root
  fill currentColor, exactly 2 paths with opacities 0.2 + 1
  (a genuine duotone), MIT licence text beside the SVGs);
- first icon resolution installs the shipped bundle (bundled
  names resolve bundled with existing files; unbundled names
  still resolve missing honestly — no lookalike substitution,
  and volume_off resolves bundled via the resolver's
  underscore->hyphen fallback to the volume-off entry, whose
  file is speaker-x-duotone.svg (volume_off decision
  2026-11-03, INTENTIONAL — see
  test_icon_underscore_mapping.py);
- an absent manifest behaves exactly as before (empty manifest,
  everything missing, bundling owed); a corrupt manifest
  installs empty AND reports the problem, never silently;
- the live loader renders bundled icons (bundled branch, not
  the template cache) and still falls back to templates for
  missing names (Qt only: rendering needs PySide6).

The manifest key is the semantic name callers ask for
(``mic``, ``search``, ...); the file is the exact Phosphor
stem (``microphone-duotone.svg``, ...). STEM maps the two.
"""

import json
import pathlib
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE_DIR = ROOT / "gui" / "assets" / "icons" / "phosphor"
BUNDLED = ["bell", "check", "clock", "database", "globe", "grid",
           "mic", "minus", "monitor", "play", "pulse", "search",
           "send", "sliders", "spark", "square", "volume",
           "volume-off", "x"]
# Semantic name -> exact Phosphor stem. Every mapping is the
# SAME glyph under a different stem name (see the bundle
# record) — never a lookalike substitution.
STEM = {"bell": "bell", "check": "check", "clock": "clock",
        "database": "database", "globe": "globe",
        "grid": "grid-four", "mic": "microphone",
        "minus": "minus", "monitor": "monitor", "play": "play",
        "pulse": "pulse", "search": "magnifying-glass",
        "send": "paper-plane-tilt",
        "sliders": "sliders-horizontal", "spark": "sparkle",
        "square": "square", "volume": "speaker-high",
        "volume-off": "speaker-x", "x": "x"}
UNBUNDLED = ["senses"]


def _register_package():
    """Mirror doctor._register_package: make voice_assistant.* importable
    from this folder whatever it is called."""
    if "voice_assistant" not in sys.modules:
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(ROOT)]
        sys.modules["voice_assistant"] = pkg


_register_package()
try:
    from core import w9_wiring as w9
    _W9_AVAILABLE = True
    _W9_ERROR = ""
except Exception as exc:  # noqa: BLE001 — PySide6 absent here
    w9 = None
    _W9_AVAILABLE = False
    _W9_ERROR = str(exc)

try:
    from PySide6.QtGui import QGuiApplication
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — PySide6 absent on this interpreter
    _QT_AVAILABLE = False


def _need_w9(test):
    if not _W9_AVAILABLE:
        test.skipTest(f"w9 deps unavailable: {_W9_ERROR}")


class TestShippedManifest(unittest.TestCase):
    def test_manifest_is_well_formed(self):
        from gui import icon_manifest as icons
        manifest_path = BUNDLE_DIR / "manifest.json"
        self.assertTrue(manifest_path.is_file())
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(e["name"] for e in data["entries"]),
                         sorted(BUNDLED))
        self.assertTrue(all(e["system"] == "phosphor"
                            for e in data["entries"]))
        manifest = {}
        for entry in data["entries"]:
            path = BUNDLE_DIR / entry["path"]
            self.assertTrue(path.is_file(), entry["name"])
            self.assertEqual(entry["path"],
                             STEM[entry["name"]] + "-duotone.svg",
                             entry["name"])
            manifest[entry["name"]] = {"system": entry["system"],
                                       "path": str(path)}
        self.assertEqual(icons.validate_manifest(manifest), [])
        built = icons.build_manifest(
            [{"name": n, "system": "phosphor",
              "path": str(BUNDLE_DIR / (STEM[n] + "-duotone.svg"))}
             for n in BUNDLED])
        self.assertEqual(built["problems"], [])
        self.assertEqual(sorted(built["manifest"]), sorted(BUNDLED))

    def test_svgs_are_valid_phosphor_duotone_art(self):
        for name in BUNDLED:
            path = BUNDLE_DIR / (STEM[name] + "-duotone.svg")
            root = ET.parse(str(path)).getroot()
            self.assertEqual(root.tag.split("}")[-1], "svg", name)
            self.assertEqual(root.get("viewBox"), "0 0 256 256", name)
            self.assertEqual(root.get("fill"), "currentColor", name)
            paths = root.findall(
                ".//{http://www.w3.org/2000/svg}path")
            self.assertEqual(len(paths), 2, name)
            self.assertEqual(
                sorted(p.get("opacity") or "1" for p in paths),
                ["0.2", "1"], name)

    def test_licence_and_record_ship_beside_the_svgs(self):
        licence = (BUNDLE_DIR / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", licence)
        self.assertIn("Phosphor", licence)
        record = (BUNDLE_DIR / "BUNDLE_RECORD.md").read_text(
            encoding="utf-8")
        self.assertIn("2b75f3ad12b420c9504ef05df8d2564a28f8500e", record)
        for name in UNBUNDLED:
            self.assertIn(name, record)


class TestResolveThroughW9(unittest.TestCase):
    def setUp(self):
        _need_w9(self)
        # Every test starts from first load, whatever ran before.
        w9._icon_manifest_validated = False
        w9._shipped_manifest_loaded = False

    def tearDown(self):
        if _W9_AVAILABLE:
            w9._icon_manifest_validated = False
            w9._shipped_manifest_loaded = False

    def test_bundled_names_resolve_bundled_with_existing_file(self):
        for name in BUNDLED:
            ref = w9.resolve_icon(name)
            self.assertTrue(ref.available, name)
            self.assertEqual(ref.status, "bundled", name)
            self.assertEqual(ref.system, "phosphor", name)
            self.assertTrue(pathlib.Path(ref.path).is_file(), name)
            self.assertTrue(
                ref.path.endswith(STEM[name] + "-duotone.svg"), name)

    def test_unbundled_names_resolve_missing_honestly(self):
        for name in UNBUNDLED:
            ref = w9.resolve_icon(name)
            self.assertFalse(ref.available, name)
            self.assertEqual(ref.status, "missing", name)

    def test_status_reports_bundle_installed(self):
        status = w9.icon_manifest_status()
        self.assertEqual(status["entries"], len(BUNDLED))
        self.assertEqual(status["problems"], [])
        self.assertFalse(status["bundling_step_owed"])

    def test_absent_manifest_falls_back_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "no-such-manifest.json"
            orig = w9._shipped_manifest_path
            w9._shipped_manifest_path = lambda: missing
            try:
                w9._icon_manifest_validated = False
                ref = w9.resolve_icon("clock")
                self.assertFalse(ref.available)
                self.assertEqual(ref.status, "missing")
                status = w9.icon_manifest_status()
                self.assertEqual(status["entries"], 0)
                self.assertTrue(status["bundling_step_owed"])
            finally:
                w9._shipped_manifest_path = orig

    def test_corrupt_manifest_reports_problem_not_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            corrupt = pathlib.Path(tmp) / "manifest.json"
            corrupt.write_text("{not json", encoding="utf-8")
            orig = w9._shipped_manifest_path
            w9._shipped_manifest_path = lambda: corrupt
            try:
                w9._icon_manifest_validated = False
                ref = w9.resolve_icon("clock")
                self.assertFalse(ref.available)
                status = w9.icon_manifest_status()
                self.assertTrue(
                    any("unreadable" in p["reason"]
                        for p in status["problems"]),
                    status)
                self.assertTrue(status["bundling_step_owed"])
            finally:
                w9._shipped_manifest_path = orig


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 unavailable")
class TestMakeIconUsesBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _need_w9(cls)
        cls._app = (QGuiApplication.instance()
                    or QGuiApplication([]))

    def setUp(self):
        w9._icon_manifest_validated = False
        w9._shipped_manifest_loaded = False
        import gui.icons as icons_mod
        icons_mod._cache.clear()

    def _has_painted_pixels(self, icon, size=20):
        pix = icon.pixmap(size, size)
        self.assertFalse(pix.isNull())
        image = pix.toImage()
        for y in range(image.height()):
            for x in range(image.width()):
                if image.pixelColor(x, y).alpha() > 0:
                    return True
        return False

    def test_bundled_icon_renders_from_bundle_not_template(self):
        import gui.icons as icons_mod
        icon = icons_mod.make_icon("clock", color="#00C8FF", size=20)
        # The bundled branch returns early and never populates the
        # template cache — so an empty cache entry proves the
        # bundle was used, not the template.
        self.assertNotIn(("clock", "#00C8FF", 20), icons_mod._cache)
        self.assertTrue(self._has_painted_pixels(icon))

    def test_missing_icon_still_falls_back_to_template(self):
        import gui.icons as icons_mod
        icon = icons_mod.make_icon("senses", color="#00C8FF", size=20)
        self.assertIn(("senses", "#00C8FF", 20), icons_mod._cache)
        self.assertTrue(self._has_painted_pixels(icon))


if __name__ == "__main__":
    unittest.main()
