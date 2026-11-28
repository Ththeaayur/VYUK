"""Phosphor Duotone icons (B-R1-6, 2026-11-09).

The gap: today's icon language is single-stroke line icons —
19 inline SVG templates in gui/icons.py (stroke 1.8, one
colour) plus a 15-SVG Lucide bundle consulted first, with 5
names falling back to templates. No duotone anywhere. This
batch replaces the Lucide bundle with the Phosphor Duotone
bundle (35 SVGs, MIT) as THE primary icon system and proves:

- every icon the set claims to cover exists as a real
  Phosphor Duotone SVG (never a placeholder): 35 files,
  viewBox 0 0 256 256, root fill currentColor, exactly 2
  paths, opacities 0.2 + 1;
- every icon renders cleanly at 16 px (the smallest size the
  UI shows) and at 24 px: non-blank, primary + secondary
  tones both present, the secondary path contributing pixels
  the primary-only render lacks, all 35 renders distinct;
- every icon is the theme's accent (Aura blue #35D6FF in the
  default theme) in BOTH tones — every painted pixel with
  alpha >= 32 is the accent within +-6 per channel
  (rasterizer rounding on overlapping semi-transparent paths;
  measured worst 5);
- every surface the set claims to cover has an icon: all 24
  call-site names resolve — 35 bundled, ``senses`` honestly
  missing from the bundle and falling back to its inline
  template (no Phosphor stem exists for the radar/sonar
  glyph — a lookalike substitution is forbidden);
- a missing icon is an honest failure: the resolver reports
  ``missing``, never a silent placeholder; a name in neither
  the bundle nor the templates raises instead of rendering
  a blank icon.

Render thresholds are MEASURED (probe 2026-11-09, Qt
offscreen, QImage ARGB32): 16 px painted 65-168 (min: pulse
65); primary band (alpha > 100) 19-104 (min: check 19);
secondary band (20-100) 4-145 (min: sliders 4); 24 px
secondary-path extra 4-300 (min: sliders 4); colour
deviation +-5 max at alpha >= 32 (measured; +-7 at alpha
>= 16 — fringe pixels carry rasterizer noise and are
excluded). Thresholds below sit under the measured minima
with margin. The four UI Phase 2 icons (shapes, heartbeat,
address-book, phone-call) were measured 2026-11-25 and fall
inside these ranges (16 px painted 118-160, primary 62-90,
secondary 40-73; 24 px extra 44-130).

Lab note: the offscreen QPixmap has NO alpha channel
(hasAlphaChannel() False), so the loader's QPixmap path
cannot be alpha-measured here — the render checks below
use QImage (the same QSvgRenderer + colour substitution
the loader performs). Transparency on real Windows, where
QPixmap has alpha, is a field leg, owed, judged by his eye.
"""

import json
import pathlib
import sys
import types
import unittest
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUNDLE_DIR = ROOT / "gui" / "assets" / "icons" / "phosphor"
BUNDLED = ["address-book", "bell", "calendar", "check", "clock",
           "cloud", "cloud-fog", "cloud-lightning", "cloud-rain",
           "cloud-snow", "cloud-sun", "currency-dollar",
           "database", "envelope", "globe", "grid", "heartbeat",
           "house", "mic", "minus", "monitor", "note", "phone-call",
           "play", "pulse", "search", "send", "shapes", "sliders",
           "spark", "square", "sun", "volume", "volume-off", "x"]
STEM = {"address-book": "address-book", "bell": "bell",
        "calendar": "calendar",
        "check": "check", "clock": "clock", "cloud": "cloud",
        "cloud-fog": "cloud-fog",
        "cloud-lightning": "cloud-lightning",
        "cloud-rain": "cloud-rain", "cloud-snow": "cloud-snow",
        "cloud-sun": "cloud-sun",
        "currency-dollar": "currency-circle-dollar",
        "database": "database", "envelope": "envelope",
        "globe": "globe",
        "grid": "grid-four", "heartbeat": "heartbeat",
        "house": "house",
        "mic": "microphone",
        "minus": "minus", "monitor": "monitor", "note": "note",
        "phone-call": "phone-call",
        "play": "play",
        "pulse": "pulse", "search": "magnifying-glass",
        "send": "paper-plane-tilt", "shapes": "shapes",
        "sliders": "sliders-horizontal", "spark": "sparkle",
        "square": "square", "sun": "sun",
        "volume": "speaker-high",
        "volume-off": "speaker-x", "x": "x"}
# Every name any call site asks for (nav + suggestions +
# literals + tool-icon mapping, audited 2026-11-27, extended
# 2026-11-25 for the UI Phase 2 nav icons).
CALL_SITE_NAMES = ["spark", "clock", "database", "pulse", "senses",
                   "check", "sliders", "monitor", "x", "volume_off",
                   "volume", "square", "send", "search", "play",
                   "minus", "mic", "grid", "bell", "globe",
                   "shapes", "heartbeat", "address-book",
                   # UI Phase 5 (2026-11-27): the three new
                   # workspace nav buttons. "currency-dollar" was
                   # already a call site (the Home Money tile) —
                   # the list was stale for it; "note" and "house"
                   # are new sidebar call sites ("house" was
                   # already used by the appearance mapping).
                   "phone-call", "note", "currency-dollar", "house"]
UNBUNDLED = ["senses"]
ACCENT = "#35D6FF"
ACCENT_RGB = (53, 214, 255)


def _register_package():
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


class TestBundleFiles(unittest.TestCase):
    def test_manifest_is_well_formed(self):
        from gui import icon_manifest as icons
        self.assertEqual(icons.PRIMARY_SYSTEM, "phosphor")
        self.assertEqual(icons.PHOSPHOR_LICENCE, "MIT")
        data = json.loads((BUNDLE_DIR / "manifest.json").read_text(
            encoding="utf-8"))
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
        self.assertIn("phosphor", icons.BUNDLING_OWED["status"])
        self.assertIn("2b75f3ad", icons.BUNDLING_OWED["status"])

    def test_every_bundled_svg_is_genuine_duotone(self):
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

    def test_licence_and_record(self):
        licence = (BUNDLE_DIR / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", licence)
        self.assertIn("Copyright (c) 2023 Phosphor Icons", licence)
        record = (BUNDLE_DIR / "BUNDLE_RECORD.md").read_text(
            encoding="utf-8")
        self.assertIn("2b75f3ad12b420c9504ef05df8d2564a28f8500e", record)
        for name in UNBUNDLED:
            self.assertIn(name, record)

    def test_no_lucide_bundle_remains(self):
        # One primary system: the Lucide bundle is gone, not
        # kept alongside Phosphor (two full systems are
        # rejected by validate_manifest).
        self.assertFalse((ROOT / "gui" / "assets" / "icons"
                          / "lucide").exists())


class TestResolve(unittest.TestCase):
    def setUp(self):
        _need_w9(self)
        w9._icon_manifest_validated = False
        w9._shipped_manifest_loaded = False

    def tearDown(self):
        if _W9_AVAILABLE:
            w9._icon_manifest_validated = False
            w9._shipped_manifest_loaded = False

    def test_every_bundled_name_resolves_bundled(self):
        for name in BUNDLED:
            ref = w9.resolve_icon(name)
            self.assertTrue(ref.available, name)
            self.assertEqual(ref.status, "bundled", name)
            self.assertEqual(ref.system, "phosphor", name)
            self.assertTrue(pathlib.Path(ref.path).is_file(), name)

    def test_every_call_site_name_is_covered_honestly(self):
        for name in CALL_SITE_NAMES:
            ref = w9.resolve_icon(name)
            if name in UNBUNDLED:
                self.assertFalse(ref.available, name)
                self.assertEqual(ref.status, "missing", name)
            else:
                self.assertTrue(ref.available, name)
                self.assertEqual(ref.status, "bundled", name)

    def test_volume_off_resolves_via_the_underscore_fallback(self):
        ref = w9.resolve_icon("volume_off")
        self.assertTrue(ref.available)
        self.assertEqual(ref.status, "bundled")
        self.assertTrue(ref.path.endswith("speaker-x-duotone.svg"))

    def test_unknown_name_is_missing_never_a_placeholder(self):
        ref = w9.resolve_icon("no_such_icon_xyz")
        self.assertFalse(ref.available)
        self.assertEqual(ref.status, "missing")

    def test_one_primary_rule_survives_the_swap(self):
        from gui import icon_manifest as icons
        manifest = {n: {"system": "phosphor", "path": "/x.svg"}
                    for n in BUNDLED}
        self.assertEqual(icons.validate_manifest(manifest), [])
        # Fluent-as-primary is still rejected: more Fluent
        # entries than Phosphor entries fails validation.
        manifest.update({f"window-close-{i}": {
            "system": "fluent", "path": "/y.svg"}
            for i in range(len(BUNDLED) + 1)})
        problems = icons.validate_manifest(manifest)
        self.assertTrue(any("two full systems" in p.reason
                            for p in problems), problems)


def _render_svg(svg_text, size):
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(0x00000000)
    renderer = QSvgRenderer(QByteArray(svg_text.encode("utf-8")))
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    return img


def _alpha_map(img):
    alphas = []
    for y in range(img.height()):
        for x in range(img.width()):
            alphas.append(img.pixelColor(x, y).alpha())
    return alphas


def _primary_only_svg(svg_text):
    root = ET.fromstring(svg_text)
    ns = "{http://www.w3.org/2000/svg}"
    for path in list(root.findall(f".//{ns}path")):
        if (path.get("opacity") or "1") == "0.2":
            root.remove(path)
    return ET.tostring(root, encoding="unicode")


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 unavailable")
class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _need_w9(cls)
        cls._app = (QGuiApplication.instance()
                    or QGuiApplication([]))

    def test_renders_cleanly_at_16px(self):
        # The smallest size the UI shows: non-blank, primary
        # tone present, secondary tone present.
        for name in BUNDLED:
            svg = (BUNDLE_DIR / (STEM[name] + "-duotone.svg")
                   ).read_text(encoding="utf-8").replace(
                       "currentColor", ACCENT)
            alphas = _alpha_map(_render_svg(svg, 16))
            painted = sum(1 for a in alphas if a > 0)
            primary = sum(1 for a in alphas if a > 100)
            secondary = sum(1 for a in alphas if 20 <= a <= 100)
            self.assertGreaterEqual(painted, 50, name)
            self.assertGreaterEqual(primary, 10, name)
            self.assertGreaterEqual(secondary, 1, name)

    def test_secondary_tone_contributes_at_24px(self):
        # The 0.2-opacity path is not decorative dead weight:
        # at 24 px it adds painted pixels the primary-only
        # render lacks, for every icon (sliders' secondary is
        # fully overlapped at 16 px — it shows at 24 px).
        for name in BUNDLED:
            svg = (BUNDLE_DIR / (STEM[name] + "-duotone.svg")
                   ).read_text(encoding="utf-8").replace(
                       "currentColor", ACCENT)
            full = sum(1 for a in _alpha_map(_render_svg(svg, 24))
                       if a > 0)
            prim = sum(1 for a in _alpha_map(
                _render_svg(_primary_only_svg(svg), 24)) if a > 0)
            self.assertGreaterEqual(full - prim, 1, name)

    def test_every_icon_is_the_theme_accent_in_both_tones(self):
        # Every painted pixel with alpha >= 32 is Aura blue,
        # never a random colour. Lower-alpha fringe pixels
        # are excluded: the rasterizer leaves noisy RGB there
        # (measured +-7 at alpha >= 16; +-5 at alpha >= 32).
        for name in BUNDLED:
            svg = (BUNDLE_DIR / (STEM[name] + "-duotone.svg")
                   ).read_text(encoding="utf-8").replace(
                       "currentColor", ACCENT)
            img = _render_svg(svg, 16)
            for y in range(16):
                for x in range(16):
                    c = img.pixelColor(x, y)
                    if c.alpha() < 32:
                        continue
                    for got, want in zip(
                            (c.red(), c.green(), c.blue()),
                            ACCENT_RGB):
                        self.assertLessEqual(abs(got - want), 6,
                                             (name, x, y, c.red(),
                                              c.green(), c.blue(),
                                              c.alpha()))

    def test_all_35_renders_are_distinct(self):
        maps = set()
        for name in BUNDLED:
            svg = (BUNDLE_DIR / (STEM[name] + "-duotone.svg")
                   ).read_text(encoding="utf-8").replace(
                       "currentColor", ACCENT)
            maps.add(tuple(_alpha_map(_render_svg(svg, 16))))
        self.assertEqual(len(maps), len(BUNDLED))

    def test_make_icon_uses_the_bundle_and_renders(self):
        import gui.icons as icons_mod
        w9._icon_manifest_validated = False
        w9._shipped_manifest_loaded = False
        icons_mod._cache.clear()
        icon = icons_mod.make_icon("clock", color=ACCENT, size=20)
        self.assertFalse(icon.isNull())
        self.assertNotIn(("clock", ACCENT, 20), icons_mod._cache)
        pix = icon.pixmap(20, 20)
        self.assertFalse(pix.isNull())
        img = pix.toImage()
        # Offscreen QPixmap has no alpha channel, so the
        # transparent background reads black: painted pixels
        # are the ones that are not black.
        painted = sum(1 for y in range(20) for x in range(20)
                      if (img.pixelColor(x, y).red(),
                          img.pixelColor(x, y).green(),
                          img.pixelColor(x, y).blue()) != (0, 0, 0))
        self.assertGreater(painted, 0)

    def test_make_icon_falls_back_to_template_for_senses(self):
        import gui.icons as icons_mod
        w9._icon_manifest_validated = False
        w9._shipped_manifest_loaded = False
        icons_mod._cache.clear()
        icon = icons_mod.make_icon("senses", color=ACCENT, size=20)
        self.assertFalse(icon.isNull())
        self.assertIn(("senses", ACCENT, 20), icons_mod._cache)

    def test_make_icon_for_unknown_name_raises_honestly(self):
        import gui.icons as icons_mod
        w9._icon_manifest_validated = False
        w9._shipped_manifest_loaded = False
        with self.assertRaises(Exception):
            icons_mod.make_icon("no_such_icon_xyz", color=ACCENT,
                                size=20)


if __name__ == "__main__":
    unittest.main()
