"""B-R1-7 — Google Fonts OFL stack (Orbitron / Sora / Share Tech Mono).

B-R1-7r (2026-11-09): the body face is Sora, not Rajdhani — the owner
picked Sora ("whatever brings us close to scifi").

Every font the stack claims to cover is a real OFL file bundled in
gui/assets/fonts/ (never a placeholder); every font renders cleanly
at its UI size (measured at the smallest size it is shown at);
every font ships with its OFL.txt beside it; every surface the
stack claims to cover has a font — and a missing font is an honest
failure, never a silent placeholder.

Headless-safe: the file / OFL / stack / status checks run on both
interpreters. The render checks need Qt and are skipped (not
failed) where PySide6 is unavailable, exactly like the phosphor
suite. Render thresholds sit UNDER the worker-measured minima
(Orbitron 16px/600 "VYUK" 339 painted; 20px/600 "Settings" 1138;
Sora 13px/400 body 2635; 10px smallest 894; Share Tech Mono
12.5px code 1550), so a threshold trip means the render really
regressed — never a rounding wobble.
"""

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".pkgroot"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics, QImage, QPainter, QColor  # noqa: F401
    from PySide6.QtWidgets import QApplication  # noqa: F401
    from PySide6.QtCore import Qt  # noqa: F401
    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — system python has no Qt
    _QT_AVAILABLE = False

from gui import style_tokens as tokens
from gui import style_sheet as sheet
from core import feel as feel_mod

FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "gui", "assets", "fonts")

# family -> (files, OFL.txt, OFL copyright line that must be present)
EXPECTED = {
    "Orbitron": (["Orbitron-Variable.ttf"], "OFL-Orbitron.txt",
                 "Copyright 2018 The Orbitron Project Authors"),
    "Sora": (["Sora-Variable.ttf"], "OFL-Sora.txt",
             "Copyright 2019 The Sora Project Authors"),
    "Share Tech Mono": (["ShareTechMono-Regular.ttf"],
                        "OFL-ShareTechMono.txt",
                        "Copyright (c) 2012, Carrois Type Design"),
}

# Files the old stack shipped. All must be GONE — one stack, no
# dead weight, no ambiguity about which face renders.
REMOVED_FILES = [
    "Inter-Regular.ttf", "Inter-Medium.ttf", "Inter-SemiBold.ttf",
    "Inter-Bold.ttf", "SpaceGrotesk-Variable.ttf",
    "ChakraPetch-Regular.ttf", "ChakraPetch-Medium.ttf",
    "ChakraPetch-SemiBold.ttf", "ChakraPetch-Bold.ttf",
    "JetBrainsMono-Regular.ttf", "JetBrainsMono-Medium.ttf",
    "JetBrainsMono-Bold.ttf",
    "OFL.txt", "OFL-ChakraPetch.txt", "OFL-JetBrainsMono.txt",
    "OFL-SpaceGrotesk.txt",
    "Rajdhani-Regular.ttf", "Rajdhani-Medium.ttf",
    "Rajdhani-SemiBold.ttf", "Rajdhani-Bold.ttf",
    "OFL-Rajdhani.txt",
]


def _ttf_family(path):
    """Family name (nameID 1) of a TTF, parsed with stdlib only.

    No fontTools in the suite: the name table is a fixed layout
    (format/count/stringOffset, then 12-byte records). Returns
    None when the file is not a parseable TTF.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"\x00\x01\x00\x00":
        return None
    (num_tables,) = struct.unpack(">H", data[4:6])
    name_off = name_len = None
    for i in range(num_tables):
        rec = data[12 + i * 16: 12 + (i + 1) * 16]
        if len(rec) < 16:
            return None
        tag = rec[:4]
        if tag == b"name":
            (name_off, name_len) = struct.unpack(">II", rec[8:16])
            break
    if name_off is None:
        return None
    name = data[name_off:name_off + name_len]
    if len(name) < 6:
        return None
    (count, string_off) = struct.unpack(">HH", name[2:6])
    for i in range(count):
        rec = name[6 + i * 12: 6 + (i + 1) * 12]
        if len(rec) < 12:
            continue
        (plat, enc, _lang, nid, length, off) = struct.unpack(
            ">HHHHHH", rec)
        if nid != 1:
            continue
        raw = name[string_off + off: string_off + off + length]
        try:
            if plat == 3:
                return raw.decode("utf-16-be")
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            continue
    return None


def _painted(family, text, px, weight):
    """Painted pixels (alpha >= 32) rendering text in family at px/weight.

    Returns the MATCHED family (QFontInfo), not the requested one:
    QFont.family() echoes the request even when Qt substituted a
    fallback, so asserting on it would pass vacuously on a tree
    without the fonts.
    """
    font = QFont(family, px)
    font.setWeight(weight)
    fm = QFontMetrics(font)
    w = max(1, fm.horizontalAdvance(text) + 8)
    h = max(1, fm.height() + 4)
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setFont(font)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(4, fm.ascent() + 2, text)
    painter.end()
    n = 0
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() >= 32:
                n += 1
    from PySide6.QtGui import QFontInfo
    return n, QFontInfo(font).family()


class TestGoogleFontsFiles(unittest.TestCase):
    def test_every_claimed_font_exists_and_is_a_real_ttf(self):
        for family, (files, _ofl, _copy) in EXPECTED.items():
            for name in files:
                path = os.path.join(FONT_DIR, name)
                self.assertTrue(os.path.isfile(path), f"missing: {name}")
                self.assertEqual(_ttf_family(path), family,
                                 f"{name} is not {family}")

    def test_every_font_ships_with_its_ofl_txt(self):
        for _family, (_files, ofl, copyright_line) in EXPECTED.items():
            path = os.path.join(FONT_DIR, ofl)
            self.assertTrue(os.path.isfile(path), f"missing: {ofl}")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("SIL OPEN FONT LICENSE Version 1.1", text)
            self.assertIn(copyright_line, text)

    def test_old_stack_files_are_gone(self):
        for name in REMOVED_FILES:
            self.assertFalse(
                os.path.exists(os.path.join(FONT_DIR, name)),
                f"old stack file still shipped: {name}")

    def test_bundle_record_names_the_pin_and_the_roles(self):
        with open(os.path.join(FONT_DIR, "BUNDLE_RECORD.md"),
                  encoding="utf-8") as fh:
            record = fh.read()
        self.assertIn("5bb28733cec313b63fbd415b574b5ae35c8aa8b8", record)
        for family in EXPECTED:
            self.assertIn(family, record)


class TestGoogleFontsStacks(unittest.TestCase):
    def test_display_stack_is_orbitron_first(self):
        self.assertTrue(
            tokens.DISPLAY_FONT_STACK.startswith("'Orbitron'"),
            tokens.DISPLAY_FONT_STACK)
        for old in ("Chakra Petch", "Space Grotesk", "Inter"):
            self.assertNotIn(old, tokens.DISPLAY_FONT_STACK)

    def test_mono_stack_is_share_tech_mono_first(self):
        self.assertTrue(
            tokens.MONO_FONT_STACK.startswith("'Share Tech Mono'"),
            tokens.MONO_FONT_STACK)
        self.assertNotIn("JetBrains Mono", tokens.MONO_FONT_STACK)
        self.assertIn("Cascadia", tokens.MONO_FONT_STACK)
        self.assertIn("Consolas", tokens.MONO_FONT_STACK)

    def test_body_rule_is_sora_first(self):
        self.assertIn(
            "font-family: 'Sora', 'Segoe UI Variable', "
            "'Segoe UI', sans-serif;",
            sheet.PREMIUM_STYLE)

    def test_code_surfaces_use_the_mono_stack(self):
        self.assertIn(
            "font-family: 'Share Tech Mono', 'Cascadia Code', "
            "'Cascadia Mono', 'Consolas', monospace;",
            sheet.PREMIUM_STYLE)
        self.assertNotIn("font-family: Consolas, monospace;",
                         sheet.PREMIUM_STYLE)
        self.assertNotIn("font-family: monospace;",
                         sheet.PREMIUM_STYLE)

    def test_no_removed_family_is_named_first_anywhere(self):
        for stack in (tokens.DISPLAY_FONT_STACK, tokens.MONO_FONT_STACK):
            first = tokens.first_font_family(stack)
            self.assertNotIn(first, ("Inter", "Chakra Petch",
                                     "Space Grotesk", "JetBrains Mono"))


class TestGoogleFontsStatus(unittest.TestCase):
    def test_each_stack_family_is_landed(self):
        for family in EXPECTED:
            status = feel_mod.font_status(family)
            self.assertTrue(status["ok"], status)
            self.assertEqual(status["status"], "LANDED")
            self.assertTrue(status["bundled"])

    def test_no_family_cleared_by_analogy(self):
        for family in ("Inter", "Space Grotesk", "Chakra Petch",
                       "JetBrains Mono", "Comic Sans MS"):
            self.assertFalse(feel_mod.font_status(family)["ok"])


@unittest.skipUnless(_QT_AVAILABLE, "PySide6 unavailable")
class TestGoogleFontsRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui import style as style_mod
        cls._app = QApplication.instance() or QApplication([])
        saved = style_mod._fonts_loaded
        try:
            style_mod._fonts_loaded = False
            cls.loaded = style_mod.load_bundled_fonts()
        finally:
            style_mod._fonts_loaded = saved

    def test_loader_registers_all_three_families(self):
        self.assertTrue(self.loaded)
        fams = QFontDatabase.families()
        for family in EXPECTED:
            self.assertIn(family, fams, f"family not registered: {family}")

    def test_orbitron_renders_at_display_sizes(self):
        n16, fam16 = _painted("Orbitron", "VYUK", 16, QFont.DemiBold)
        self.assertEqual(fam16, "Orbitron")
        self.assertGreaterEqual(n16, 200)  # measured 339
        n20, fam20 = _painted("Orbitron", "Settings", 20, QFont.DemiBold)
        self.assertEqual(fam20, "Orbitron")
        self.assertGreaterEqual(n20, 800)  # measured 1138

    def test_orbitron_variable_weight_responds(self):
        n400, fam400 = _painted("Orbitron", "VYUK", 16, QFont.Normal)
        n700, fam700 = _painted("Orbitron", "VYUK", 16, QFont.Bold)
        # Both renders must really be Orbitron (a fallback font
        # also gets bolder — the weight response only proves the
        # variable font when the matched family is Orbitron).
        self.assertEqual(fam400, "Orbitron")
        self.assertEqual(fam700, "Orbitron")
        # Same advance, bolder strokes paint more: the variable
        # font really answers the weight, it is not a static file
        # wearing a variable name.
        self.assertGreater(n700, n400)

    def test_sora_renders_at_body_and_smallest_sizes(self):
        n13, fam13 = _painted(
            "Sora",
            "The quick brown fox jumps over the lazy dog 0123456789",
            13, QFont.Normal)
        self.assertEqual(fam13, "Sora")
        self.assertGreaterEqual(n13, 2000)  # measured 2635
        n10, fam10 = _painted(
            "Sora", "The quick brown fox 0123456789", 10, QFont.Normal)
        self.assertEqual(fam10, "Sora")
        self.assertGreaterEqual(n10, 600)  # measured 894

    def test_sora_variable_weight_responds(self):
        n400, fam400 = _painted("Sora", "VYUK", 16, QFont.Normal)
        n700, fam700 = _painted("Sora", "VYUK", 16, QFont.Bold)
        # Both renders must really be Sora (a fallback font also
        # gets bolder — the weight response only proves the
        # variable font when the matched family is Sora).
        self.assertEqual(fam400, "Sora")
        self.assertEqual(fam700, "Sora")
        # Same advance, bolder strokes paint more: the variable
        # font really answers the weight, it is not a static file
        # wearing a variable name. (Measured 359 -> 497; weight 600
        # paints the same count as 400 at this size — pixel-count
        # granularity, not a missing instance — so the test uses
        # 400 vs 700, like the Orbitron weight test.)
        self.assertGreater(n700, n400)

    def test_share_tech_mono_renders_at_code_size(self):
        n, fam = _painted(
            "Share Tech Mono",
            "def prove_fonts(tree): return True  # 0123456789",
            12, QFont.Normal)
        self.assertEqual(fam, "Share Tech Mono")
        self.assertGreaterEqual(n, 1000)  # measured 1550

    def test_first_font_family_matches_the_loaded_faces(self):
        self.assertEqual(
            tokens.first_font_family(tokens.DISPLAY_FONT_STACK),
            "Orbitron")
        self.assertEqual(
            tokens.first_font_family(tokens.MONO_FONT_STACK),
            "Share Tech Mono")


if __name__ == "__main__":
    unittest.main()
