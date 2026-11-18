"""Command Deck Phase B tests (guide §10 Phase B, §4 D1–D4, §11.8).

Pure tests run on both interpreters (style.py and tokens.py are
stdlib-only at import; both are loaded by file path and injected
into sys.modules under the voice_assistant shim so
prove_byte_identity()'s lazy style import resolves). Qt tests
(orb palettes + widget defaults) skip unless PySide6 imports and a
QApplication can be created.

What is pinned here:
* the INV-11 byte-identity proof is GREEN for the new values
  (it was red on the Phase A tree: the generator derived pressed
  at 18% toward black while the shipped resolver uses 12% —
  reconciled in Phase B to the shipped 0.12, see DECISIONS.md);
* the guide §5.2 palette, in both token sources;
* the 11-theme set (Indigo Teal renamed to Cyan; Amber + Crimson
  added) with identical values in DTCG and style THEMES;
* the D3 migration helper and the D4 custom-accent gate;
* >= 4.5:1 text contrast for every text-on-surface pair in every
  shipped theme (guide §11.8);
* buttons carry no gradients in the house sheet;
* theme-pack validation still accepts a valid pack and rejects an
  unknown token whole (vocabulary unchanged by the re-pin);
* the font stacks (Orbitron / Share Tech Mono first, with the
  guide's fallbacks) and a no-raise font loader.
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent

# L3 lesson: snapshot exactly the sys.modules keys this module
# touches and restore them — never delete-all voice_assistant.*
_SAVED_MODULES = {}


def _load_by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def setUpModule():
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [str(TREE)]
    core_pkg = types.ModuleType("voice_assistant.core")
    core_pkg.__path__ = [str(TREE / "core")]
    gui_pkg = types.ModuleType("voice_assistant.gui")
    gui_pkg.__path__ = [str(TREE / "gui")]
    for key, mod in (
        ("voice_assistant", pkg),
        ("voice_assistant.core", core_pkg),
        ("voice_assistant.gui", gui_pkg),
    ):
        _SAVED_MODULES[key] = sys.modules.get(key, None)
        sys.modules[key] = mod
    style = _load_by_path(
        "voice_assistant.gui.style", str(TREE / "gui" / "style.py"))
    tokens = _load_by_path(
        "voice_assistant.core.tokens",
        str(TREE / "core" / "tokens.py"))
    packs = _load_by_path(
        "theme_packs_under_test", str(TREE / "core" / "theme_packs.py"))
    globals()["style"] = style
    globals()["tokens"] = tokens
    globals()["theme_packs"] = packs


def tearDownModule():
    for key, saved in _SAVED_MODULES.items():
        if saved is None:
            sys.modules.pop(key, None)
        else:
            sys.modules[key] = saved
    for key in ("voice_assistant.gui.style",
                "voice_assistant.core.tokens",
                "theme_packs_under_test"):
        sys.modules.pop(key, None)


EXPECTED_THEMES = (
    "Cyan", "Sunset", "Violet", "Graphite", "Aurora", "Ember",
    "Mono", "Rose", "Contrast", "Amber", "Crimson",
)


class TestProofGreen(unittest.TestCase):
    def test_proof_identical_all(self):
        # GAPS-15: prove_byte_identity takes the shipped style as an
        # argument now (core must not import GUI); this test loaded
        # `style` by path already, so it passes it in. Same proof.
        proof = tokens.prove_byte_identity(style)
        self.assertTrue(
            proof["identical_all"],
            f"INV-11 proof must be green after the Phase B re-pin: "
            f"{proof['themes']}",
        )
        self.assertTrue(proof["house"]["identical"])
        self.assertEqual(
            sorted(proof["themes"]), sorted(EXPECTED_THEMES))

    def test_no_indigo_teal_theme(self):
        self.assertNotIn("Indigo Teal", tokens.theme_names())
        self.assertNotIn("Indigo Teal", style.THEMES)


class TestPalette(unittest.TestCase):
    def test_chrome_values(self):
        self.assertEqual(tokens._BG, "#04090E")
        self.assertEqual(tokens._SURFACE_1, "#0C1D28")
        self.assertEqual(tokens._SURFACE_2, "#08131B")
        self.assertEqual(tokens._SURFACE_3, "#122636")
        self.assertEqual(tokens._TEXT, "#E6FBFF")
        self.assertEqual(tokens._TEXT_MUTED, "#B0C1CD")
        self.assertEqual(tokens._TEXT_FAINT, "#7E9AA6")
        self.assertEqual(style.BG, "#04090E")
        self.assertEqual(style.SURFACE_1, "#0C1D28")
        self.assertEqual(style.SURFACE_2, "#08131B")
        self.assertEqual(style.SURFACE_3, "#122636")
        self.assertEqual(style.TEXT, "#E6FBFF")
        self.assertEqual(style.TEXT_MUTED, "#B0C1CD")
        self.assertEqual(style.TEXT_FAINT, "#7E9AA6")

    def test_role_colours(self):
        self.assertEqual(tokens.CYAN_ACCENT, "#35D6FF")
        self.assertEqual(tokens._AMBER, "#FFB020")
        self.assertEqual(tokens._OK, "#5CF2A8")
        self.assertEqual(tokens._DANGER, "#FF5468")
        self.assertEqual(tokens._NEUTRAL, "#7E9AA6")
        self.assertEqual(tokens._LINE_SUBTLE, "rgba(53, 214, 255, 36)")
        self.assertEqual(tokens._LINE_STRONG, "rgba(53, 214, 255, 102)")
        self.assertEqual(style.ACCENT, "#35D6FF")
        self.assertEqual(style.ACCENT_2, "#3D8BFF")
        self.assertEqual(style.AMBER, "#FFB020")
        self.assertEqual(style.OK, "#5CF2A8")
        self.assertEqual(style.DANGER, "#FF5468")
        self.assertEqual(style.DANGER_TEXT, "#FF5468")
        self.assertEqual(style.NEUTRAL, "#7E9AA6")
        self.assertEqual(style.LINE_SUBTLE, "rgba(53, 214, 255, 36)")
        self.assertEqual(style.LINE_STRONG, "rgba(53, 214, 255, 102)")
        self.assertEqual(style.FOCUS_BORDER, "1px solid #35D6FF")
        self.assertEqual(style.TEXT_DISABLED, "rgba(230, 251, 255, 115)")

    def test_new_themes_match_in_both_sources(self):
        cases = {
            "Cyan": ("#35D6FF", "#3D8BFF", "#E6FBFF"),
            "Amber": ("#FFB020", "#FF6A00", "#FFF1D6"),
            "Crimson": ("#FF5468", "#FD465A", "#FFE3E6"),
        }
        for name, (accent, accent_2, highlight) in cases.items():
            entry = style.THEMES[name]
            self.assertEqual(
                (entry["accent"], entry["accent_2"], entry["highlight"]),
                (accent, accent_2, highlight))
            resolved = tokens.build_python_tokens(name)
            self.assertEqual(
                (resolved["accent"], resolved["accent_2"],
                 resolved["highlight"]),
                (accent, accent_2, highlight))

    def test_violet_keeps_its_own_secondary(self):
        # Violet's indigo secondary is that theme's identity, not
        # the retired house role — the re-pin must not touch it.
        self.assertEqual(style.THEMES["Violet"]["accent_2"], "#7C8CFF")
        self.assertEqual(
            tokens.build_python_tokens("Violet")["accent_2"], "#7C8CFF")

    def test_house_spellings(self):
        expected = {
            "_HOUSE_ACCENT_HOVER": "#5DDEFF",
            "_HOUSE_ACCENT_2_HOVER": "#64A2FF",
            "_HOUSE_ACCENT_PRESSED": "#2FBCE0",
            "_HOUSE_ACCENT_2_PRESSED": "#367AE0",
            "_HOUSE_PALE": "#E6FBFF",
            "_HOUSE_DISABLED_A": "#185B6E",
            "_HOUSE_DISABLED_B": "#1B3D6E",
        }
        for attr, value in expected.items():
            self.assertEqual(getattr(style, attr), value)
            self.assertEqual(getattr(tokens, attr), value)

    def test_pressed_derivation_is_012(self):
        resolved = tokens.build_python_tokens("Cyan")
        self.assertEqual(resolved["accent_pressed"], "#2FBCE0")
        self.assertEqual(resolved["accent_2_pressed"], "#367AE0")
        shipped = style._resolve_theme(style.THEMES["Cyan"])
        self.assertEqual(shipped["accent_pressed"], "#2FBCE0")
        self.assertEqual(shipped["accent_2_pressed"], "#367AE0")


class TestMigrationAndGate(unittest.TestCase):
    def test_migrate_theme_name(self):
        migrate = tokens.migrate_theme_name
        self.assertEqual(migrate("Indigo Teal"), "Cyan")
        self.assertEqual(migrate("indigo teal"), "Cyan")
        self.assertEqual(migrate("  Indigo Teal  "), "Cyan")
        self.assertEqual(migrate("Cyan"), "Cyan")
        self.assertEqual(migrate("Sunset"), "Sunset")
        self.assertEqual(migrate("Custom"), "Custom")
        self.assertIsNone(migrate(None))
        self.assertEqual(migrate(123), 123)

    def test_gate_keeps_readable_accents(self):
        gate = tokens.gate_custom_accent
        self.assertEqual(gate("#35D6FF"), "#35D6FF")
        self.assertEqual(gate("#ffb020"), "#FFB020")
        self.assertEqual(gate("#FF5468"), "#FF5468")

    def test_gate_falls_back_to_cyan(self):
        gate = tokens.gate_custom_accent
        # Near-black on near-black BG: 1.58:1.
        self.assertEqual(gate("#333333"), "#35D6FF")
        # A near-miss blue as a button fill: 4.38:1, just under
        # the floor — the gate must catch near misses too. (This
        # was the first Phase B accent_2 candidate; it failed the
        # tree's own uipolish guards, hence #3D8BFF.)
        self.assertEqual(gate("#0A6CFF"), "#35D6FF")
        self.assertEqual(gate("not-a-colour"), "#35D6FF")
        self.assertEqual(gate(""), "#35D6FF")
        self.assertEqual(gate(None), "#35D6FF")

    def test_contrast_floor(self):
        # BG-coloured button text on the cyan role itself: 11.63:1.
        self.assertGreaterEqual(
            tokens.contrast_ratio("#04090E", "#35D6FF"), 4.5)


class TestContrastAllThemes(unittest.TestCase):
    def test_every_text_pair_in_every_theme(self):
        for name in EXPECTED_THEMES:
            resolved = style._resolve_theme(style.THEMES[name])
            for text_key in ("text", "text_muted", "text_faint"):
                for surface_key in ("bg", "surface_1", "surface_2"):
                    ratio = tokens.contrast_ratio(
                        resolved[surface_key], resolved[text_key])
                    self.assertGreaterEqual(
                        ratio, 4.5,
                        f"{name} {text_key} on {surface_key}: {ratio:.2f}")

    def test_custom_cyan_theme_pairs(self):
        resolved = style._resolve_theme(style.derive_theme("#35D6FF"))
        for text_key in ("text", "text_muted", "text_faint"):
            ratio = tokens.contrast_ratio(resolved["bg"],
                                          resolved[text_key])
            self.assertGreaterEqual(ratio, 4.5)

    def test_contrast_implementations_agree(self):
        pairs = [
            ("#04090E", "#35D6FF"), ("#04090E", "#333333"),
            ("#0C1D28", "#E6FBFF"), ("#000000", "#CFCFCF"),
            ("#FFFFFF", "#000000"), ("#FFB020", "#04090E"),
        ]
        for a, b in pairs:
            self.assertAlmostEqual(
                tokens.contrast_ratio(a, b),
                theme_packs.contrast_ratio(a, b), places=9)


class TestButtonsNoGradients(unittest.TestCase):
    def _section(self, sheet, start, end):
        i = sheet.index(start)
        j = sheet.index(end, i)
        return sheet[i:j]

    def test_primary_mic_send_have_no_gradient(self):
        sheet = style.PREMIUM_STYLE
        primary = self._section(
            sheet, "QPushButton#primaryBtn {", "QPushButton#dangerBtn")
        self.assertNotIn("qlineargradient", primary)
        self.assertIn("border: 1px solid #35D6FF", primary)
        mic = self._section(
            sheet, "QPushButton#micBtn {", "QPushButton#sendBtn")
        self.assertNotIn("qlineargradient", mic)
        send = self._section(
            sheet, "QPushButton#sendBtn {", "QPushButton#micBtn:focus")
        self.assertNotIn("qlineargradient", send)

    def test_brand_mark_is_solid_accent(self):
        sheet = style.PREMIUM_STYLE
        mark = self._section(
            sheet, "QLabel#brandMark {", "QLabel#brandName")
        self.assertIn("background: #35D6FF", mark)
        self.assertNotIn("qlineargradient", mark)

    def test_checked_toggle_is_solid_accent(self):
        sheet = style.PREMIUM_STYLE
        toggle = self._section(
            sheet, "QCheckBox::indicator:checked {",
            "QCheckBox::indicator:disabled")
        self.assertIn("background: #35D6FF", toggle)
        self.assertNotIn("qlineargradient", toggle)

    def test_no_primary_gradient_token_remains(self):
        for attr in ("PRIMARY_GRADIENT", "PRIMARY_GRADIENT_HOVER",
                     "PRIMARY_GRADIENT_PRESSED",
                     "PRIMARY_GRADIENT_REVERSED",
                     "PRIMARY_GRADIENT_DISABLED", "PRIMARY_RIM_FOCUS"):
            self.assertFalse(hasattr(style, attr), attr)
        self.assertIn("PRIMARY_WASH", style._TOKENS)


class TestThemePacksStillValidate(unittest.TestCase):
    def _valid_pack(self):
        return {
            "schema_version": theme_packs.SCHEMA_VERSION,
            "name": "Phase B sample",
            "tokens": {
                "surface": "#0C1D28",
                "surface_variant": "#08131B",
                "on_surface": "#E6FBFF",
                "primary": "#35D6FF",
                "on_primary": "#04090E",
                "accent": "#35D6FF",
            },
        }

    def test_valid_pack_passes(self):
        self.assertEqual(theme_packs.validate_pack(self._valid_pack()), [])

    def test_unknown_token_rejected_whole(self):
        pack = self._valid_pack()
        pack["tokens"]["indigo_teal"] = "#7C8CFF"
        failures = theme_packs.validate_pack(pack)
        self.assertTrue(any("indigo_teal" in str(f) for f in failures))

    def test_pack_accent_flows_through_gate(self):
        # A valid pack's accent becomes the live Custom accent; the
        # D4 gate still applies at apply time.
        pack = self._valid_pack()
        self.assertEqual(
            tokens.gate_custom_accent(pack["tokens"]["accent"]),
            "#35D6FF")
        pack["tokens"]["accent"] = "#333333"
        self.assertEqual(
            tokens.gate_custom_accent(pack["tokens"]["accent"]),
            "#35D6FF")


class TestFontStacks(unittest.TestCase):
    def test_display_stack_is_orbitron_first(self):
        # B-R1-7: Orbitron replaced Chakra Petch / Space Grotesk /
        # Inter as the display face (one stack; the old files are
        # removed, so no old family may appear in the stack).
        self.assertTrue(
            style.DISPLAY_FONT_STACK.startswith("'Orbitron'"))
        for old in ("Chakra Petch", "Space Grotesk", "Inter"):
            self.assertNotIn(old, style.DISPLAY_FONT_STACK)

    def test_mono_stack_is_share_tech_mono_first(self):
        # B-R1-7: Share Tech Mono replaced JetBrains Mono as the
        # mono face; the platform fallbacks stay.
        self.assertTrue(
            style.MONO_FONT_STACK.startswith("'Share Tech Mono'"))
        self.assertNotIn("JetBrains Mono", style.MONO_FONT_STACK)
        self.assertIn("Cascadia", style.MONO_FONT_STACK)
        self.assertIn("Consolas", style.MONO_FONT_STACK)

    def test_font_loader_never_raises(self):
        # The loader's cache flag is process-global and earlier
        # suites may have loaded fonts / created an app, so this
        # test must not assume a fresh process: it pins the guard
        # exactly where the guard applies (no application exists),
        # and the with-app success path, restoring the flag after.
        saved = style._fonts_loaded
        try:
            style._fonts_loaded = False
            result = style.load_bundled_fonts()
            self.assertIsInstance(result, bool)
            no_app = True
            if _QT_AVAILABLE:
                from PySide6.QtGui import QGuiApplication
                no_app = QGuiApplication.instance() is None
            if no_app:
                # No app: False on both interpreters (ImportError
                # path on system python; the no-application guard
                # under Qt — calling QFontDatabase with no app
                # segfaults, found by this test).
                self.assertFalse(result)
            if _QT_AVAILABLE:
                from PySide6.QtWidgets import QApplication
                QApplication.instance() or QApplication([])
                # With an app, the bundled Orbitron + Sora +
                # Share Tech Mono files exist, so the loader
                # succeeds (the old Inter / Space Grotesk /
                # Chakra Petch / JetBrains Mono files are removed
                # by B-R1-7 — the loader skips absent files).
                style._fonts_loaded = False
                self.assertTrue(style.load_bundled_fonts())
        finally:
            style._fonts_loaded = saved


try:
    from PySide6.QtWidgets import QApplication as _QApp
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — system python has no Qt
    _QT_AVAILABLE = False


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 unavailable")
class TestOrbPalettesQt(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_orb_theme_table(self):
        from voice_assistant.gui.visualizer import THEMES
        self.assertNotIn("Indigo Teal", THEMES)
        self.assertEqual(THEMES["Cyan"], ("#E6FBFF", "#35D6FF", "#3D8BFF"))
        self.assertEqual(THEMES["Amber"], ("#FFF1D6", "#FFB020", "#FF6A00"))
        self.assertEqual(
            THEMES["Crimson"], ("#FFE3E6", "#FF5468", "#FD465A"))

    def test_orb_widget_defaults(self):
        from voice_assistant.gui.visualizer import OrbVisualizer
        orb = OrbVisualizer()
        self.assertEqual(orb.theme_name, "Cyan")
        self.assertEqual(orb.orb_style, "Aura")

    def test_mini_orb_defaults(self):
        from voice_assistant.gui.mini_orb import THEMES, _AuraMiniCanvas
        self.assertNotIn("Indigo Teal", THEMES)
        self.assertEqual(THEMES["Cyan"], ("#E6FBFF", "#35D6FF", "#3D8BFF"))
        canvas = _AuraMiniCanvas()
        self.assertEqual(canvas.theme_name, "Cyan")
        self.assertEqual(canvas.orb_style, "Aura")


if __name__ == "__main__":
    unittest.main()
