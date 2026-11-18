"""Underscore->hyphen icon mapping (volume_off decision item).

Decision (parent, code-rulings delegation, 2026-11-03): the
underscore->hyphen mapping lives in the icon loader — NOT a
rename of the loader name. Callers keep asking for
``volume_off``; the resolver tries the hyphenated Phosphor stem
``volume-off`` as an exact manifest key.

B-R1-6 (2026-11-09): the bundle is now Phosphor Duotone —
``volume-off`` maps to ``speaker-x-duotone.svg`` (the
speaker-with-X glyph), ``volume`` to ``speaker-high-duotone.svg``.
The mechanism is unchanged; only the bundle dir, the system
name, the file stems, and the unbundled list moved (the
Lucide bundle, 15 SVGs, is removed).

Bundle half LANDED 2026-11-03: the bundle now holds 15 SVGs
including volume-off.svg (the owner saved it from the pinned
commit URL after automated fetches failed, and uploaded it),
so ``volume_off`` resolves bundled through the mapping with
no further code change, and the mute button renders from the
bundle instead of the template. The history is kept here so
the mapping's origin stays auditable: the original brief
assumed ``volume-off.svg`` already existed in the bundle. It
did NOT — the bundle held 14 SVGs and no volume-off file,
and the pinned-commit raw fetch timed out during the mapping
batch and was not retried.
"""

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gui import icon_manifest as icons  # noqa: E402

BUNDLE_DIR = ROOT / "gui" / "assets" / "icons" / "phosphor"
BUNDLED = ["bell", "check", "clock", "database", "globe", "grid",
           "mic", "minus", "monitor", "play", "pulse", "search",
           "send", "sliders", "spark", "square", "volume",
           "volume-off", "x"]
# Semantic name -> exact Phosphor stem (same glyph, different
# stem name — see the bundle record).
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


def _real_manifest():
    data = json.loads((BUNDLE_DIR / "manifest.json").read_text(
        encoding="utf-8"))
    manifest = {}
    for entry in data["entries"]:
        manifest[entry["name"]] = {"system": entry["system"],
                                   "path": str(BUNDLE_DIR / entry["path"])}
    return manifest


class TestUnderscoreHyphenMapping(unittest.TestCase):
    def test_fallback_hits_hyphenated_entry(self):
        # The mapping itself, against a manifest that HAS the
        # hyphenated entry (the real shipped state since the
        # bundle half landed): volume_off resolves bundled to the
        # volume-off file, under the caller's own name.
        manifest = {"volume-off": {"system": "phosphor",
                                   "path": str(BUNDLE_DIR / "speaker-x-duotone.svg")}}
        ref = icons.resolve("volume_off", manifest)
        self.assertTrue(ref.available)
        self.assertEqual(ref.status, "bundled")
        self.assertEqual(ref.system, "phosphor")
        self.assertEqual(ref.name, "volume_off")
        self.assertTrue(ref.path.endswith("speaker-x-duotone.svg"))

    def test_real_manifest_resolves_volume_off_bundled(self):
        # Bundle half landed 2026-11-03: the shipped manifest
        # carries volume-off, so volume_off resolves bundled
        # through the fallback, under the caller's own name.
        # (This replaces the earlier missing-honesty pin, which
        # must never have been weakened while the file was
        # absent — it was replaced only because the file
        # is now really there.)
        ref = icons.resolve("volume_off", _real_manifest())
        self.assertTrue(ref.available)
        self.assertEqual(ref.status, "bundled")
        self.assertEqual(ref.system, "phosphor")
        self.assertEqual(ref.name, "volume_off")
        self.assertTrue(ref.path.endswith("speaker-x-duotone.svg"))

    def test_full_set_resolutions_on_real_manifest(self):
        # Full-set identity proof: every bundled name resolves
        # bundled to its own file (volume_off via the fallback
        # to volume-off.svg), every unbundled name still
        # resolves missing.
        manifest = _real_manifest()
        for name in BUNDLED:
            ref = icons.resolve(name, manifest)
            self.assertTrue(ref.available, name)
            self.assertEqual(ref.status, "bundled", name)
            self.assertTrue(
                ref.path.endswith(STEM[name] + "-duotone.svg"), name)
        for name in UNBUNDLED:
            ref = icons.resolve(name, manifest)
            self.assertFalse(ref.available, name)
            self.assertEqual(ref.status, "missing", name)

    def test_exact_hit_always_wins_over_fallback(self):
        # A manifest carrying BOTH keys resolves the exact
        # key — the fallback never shadows an exact hit.
        manifest = {"volume_off": {"system": "phosphor",
                                   "path": "/exact/volume_off.svg"},
                    "volume-off": {"system": "phosphor",
                                   "path": "/fallback/speaker-x-duotone.svg"}}
        ref = icons.resolve("volume_off", manifest)
        self.assertEqual(ref.path, "/exact/volume_off.svg")

    def test_no_lookalike_substitution(self):
        # The fallback is exact-key aliasing only: names with
        # no hyphenated manifest entry stay missing, even
        # when a near neighbour is bundled.
        manifest = {"volume-off": {"system": "phosphor",
                                   "path": str(BUNDLE_DIR / "speaker-x-duotone.svg")}}
        for name in ("senses", "volume_up", "volume_down"):
            ref = icons.resolve(name, manifest)
            self.assertFalse(ref.available, name)
            self.assertEqual(ref.status, "missing", name)

    def test_fallback_validates_the_hyphenated_stem(self):
        # Transliterations that are not valid kebab-case
        # (trailing/leading hyphens, double hyphens) stay
        # missing instead of resolving or raising.
        manifest = {"volume-off": {"system": "phosphor",
                                   "path": str(BUNDLE_DIR / "speaker-x-duotone.svg")}}
        for name in ("volume_", "_volume_off", "volume__off",
                     "volume_off_"):
            ref = icons.resolve(name, manifest)
            self.assertFalse(ref.available, name)
            self.assertEqual(ref.status, "missing", name)

    def test_hyphenated_request_needs_no_fallback(self):
        # A direct hyphenated request is an exact hit, today
        # and after the bundle half lands — the fallback is
        # only for underscore callers.
        manifest = {"volume-off": {"system": "phosphor",
                                   "path": str(BUNDLE_DIR / "speaker-x-duotone.svg")}}
        ref = icons.resolve("volume-off", manifest)
        self.assertTrue(ref.available)
        self.assertEqual(ref.name, "volume-off")
        self.assertTrue(ref.path.endswith("speaker-x-duotone.svg"))


if __name__ == "__main__":
    unittest.main()
