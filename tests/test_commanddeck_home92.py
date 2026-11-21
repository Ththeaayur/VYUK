"""Command Deck guide §9.2 Home — the batch landed in Phase E.

The guide's Phase D Home rebuild (icon rail, hero rewrite,
transcript restyle, orb sizing, CommandRadar, Windows finish)
is larger than one phase's tail; this suite pins ONLY the
§9.2 items actually built here, so the owed remainder stays
visible in the Phase E report instead of hiding behind a
suite that claims more than it proves. Behaviour beyond these
source pins is proved by the Phase E renders (dock_flow shot:
48 bars live in the dock; home_listening crop: square Mute /
Screen buttons, ›_ prompt).

Built here (§9.2 Dock, Waveform):
* 48 cyan bars in the dock waveform (was 18).
* ›_ prompt glyph on the composer placeholder.
* Square Mute / Screen transport buttons (squareBtn rule).
* Aura-only default (owner decision 2026-11-01: Apex and
  every other style deleted; saved non-Aura styles migrate
  to Aura on load).
"""

import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent


def _src(rel):
    return (TREE / rel).read_text(encoding="utf-8")


class DockWaveformTests(unittest.TestCase):
    def test_forty_eight_bars(self):
        # INTENTIONAL (refactor P1 step 5): WaveformWidget moved verbatim
        # to window_widgets_chatbits.py. Assertions unchanged.
        src = _src("gui/window_widgets_chatbits.py")
        self.assertIn("_BAR_COUNT = 48", src)
        self.assertIn("[0.0] * self._BAR_COUNT", src)
        self.assertIn("int(i / self._BAR_COUNT * 15)", src)


class DockChromeTests(unittest.TestCase):
    def test_prompt_glyph(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        # Chat UI build (2026-11-16, move 6): the composer placeholder
        # invites typing or the mic in plain words now — "Type, or
        # hold mic" replaces the ›_ prompt glyph. E-F103
        # (2026-11-20): shortened from "Type, or hold the mic to
        # speak" (195 px — it wrapped and read "Type, or" at every
        # width); the mic instruction survives in "hold mic".
        shell = _src("gui/window_shell_assistant.py")
        self.assertIn("Type, or hold mic", shell)

    def test_square_transport_buttons(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        shell = _src("gui/window_shell_assistant.py")
        self.assertIn('self.btn_mute.setObjectName("squareBtn")', shell)
        self.assertIn('self.btn_screen.setObjectName("squareBtn")', shell)
        # INTENTIONAL (GUI split #6): the squareBtn rules live in the QSS
        # template, which moved verbatim from gui/style.py to
        # gui/style_sheet.py. Assertions unchanged.
        style = _src("gui/style_sheet.py")
        self.assertIn("QPushButton#squareBtn {", style)
        self.assertIn("border-radius: 0px", style)
        self.assertIn("QPushButton#squareBtn:focus", style)


class AuraDefaultTests(unittest.TestCase):
    def test_fresh_install_defaults_to_aura(self):
        # INTENTIONAL (GUI split #7): __init__ moved verbatim to
        # visualizer_state.py; the Aura-default check follows it.
        viz = _src("gui/visualizer_state.py")
        self.assertIn('self.orb_style = "Aura"', viz)
        # INTENTIONAL (refactor P1 step 5): migrate_orb_style moved
        # verbatim to window_settings_appearance.py. Assertions unchanged.
        settings = _src("gui/window_settings_appearance.py")
        self.assertIn("migrate_orb_style", settings)
        self.assertIn('qsettings_set("orb_style", _style', settings)
        app_settings = _src("core/app_settings.py")
        self.assertIn('ORB_STYLES: tuple[str, ...] = ("Aura",)', app_settings)


if __name__ == "__main__":
    unittest.main()
