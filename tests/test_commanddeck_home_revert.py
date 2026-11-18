"""Home revert to Phase E — owner verdict 2026-11-01 ~03:32 IST.

The owner rejected the §9.2 home (~01:53 IST) and the drawer rework
(~03:28 IST), then replied to the Phase E render
(commanddeck_evidence/phaseE/home_listening.png) with "this one is
perfect". Latest word governs: Home is the Phase E home
(backup_commanddeck_home92_pre), NOT v5.99 and NOT the §9.2/rework home.

These source pins guard the revert's load-bearing claims, in the same
style as the Phase E suite: the 220 px sidebar (not the 72/224 rail),
the chat panel DOCKED in the home splitter (not a drawer), and the
absence of the §9.2/rework-only inventions (S8 corner telemetry,
transcript drawer + Ctrl+T drawer shortcut). Behaviour beyond these
pins is proved by the revert renders compared against the Phase E
render (see commanddeck_evidence/PROGRESS.md revert record).
"""

import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent


def _src(rel):
    return (TREE / rel).read_text(encoding="utf-8")


def _gui_src():
    out = []
    for path in sorted((TREE / "gui").rglob("*.py")):
        out.append(path.read_text(encoding="utf-8"))
    return "\n".join(out)


class PhaseEHomeTests(unittest.TestCase):
    def test_sidebar_is_220_not_rail(self):
        # INTENTIONAL (refactor P1 step 5): the sidebar moved verbatim
        # to window_shell_sidebar.py. Assertions unchanged.
        shell = _src("gui/window_shell_sidebar.py")
        self.assertIn("sidebar.setFixedWidth(220)", shell)
        self.assertNotIn("setFixedWidth(72)", shell)
        self.assertNotIn("setFixedWidth(224 if wide else 72)", shell)

    def test_chat_panel_docked_in_splitter(self):
        # INTENTIONAL (refactor P1 step 5): the assistant page moved
        # verbatim to window_shell_assistant.py. Assertions unchanged.
        shell = _src("gui/window_shell_assistant.py")
        self.assertIn('self.chat_panel.setObjectName("chatPanel")', shell)
        self.assertIn("split.addWidget(self.chat_panel)", shell)

    def test_no_s8_corner_telemetry(self):
        gui = _gui_src()
        self.assertNotIn("_refresh_corner_telemetry", gui)
        self.assertNotIn("corner_telemetry", gui)
        self.assertNotIn("LAST CAPTURE", gui)

    def test_no_transcript_drawer(self):
        gui = _gui_src()
        self.assertNotIn("TranscriptDrawer", gui)
        self.assertNotIn("transcript_drawer", gui)
        self.assertNotIn("drawer_toggle", gui)
        widgets = _src("gui/window_widgets.py")
        self.assertNotIn("Open or close the transcript drawer", widgets)
        self.assertNotIn('("Ctrl+T",', widgets)


if __name__ == "__main__":
    unittest.main()
