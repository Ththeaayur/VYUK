"""Round-3 section E, E-F207: Help card still calls the disclosure
"More settings" (renamed "Advanced").

Two FAQ entries in gui/pf_settings_help.py sent the user to a
disclosure that no longer exists — and the guard test
tests/test_uis4.py::test_no_more_settings_user_strings passed anyway,
because the phrase was split across adjacent string literals
("Settings → More " + "settings → ...") and the raw-text assertNotIn
never saw the contiguous phrase while the rendered text said it.

MEASURED CORRECTION to the filed direction: the two cards are NOT
behind Advanced. PermissionsWidget and PrivacyDashboardWidget
("Your stored facts") live in _g_privacy — the "Privacy &
permissions" EVERYDAY group (gui/window_settings.py:660,
_group() adds every group box to the main page c_layout;
gui/window_settings_groups.py:212-214, "beside Privacy & security,
per the PF placement table"). "Settings → Advanced → Permissions"
would send the user to the wrong place, so the entries now read
"Settings → Privacy & permissions → Permissions" and
"Settings → Privacy & permissions → Your stored facts", matching
the file's own "where" vocabulary (pf_settings_help.py:73).

The guard fix (in test_uis4.py, same change): check every string
CONSTANT with ast — the parser folds implicit concatenation, so a
constant IS the rendered text and the split-line shape cannot hide.
Comments are not constants, so the one historical rename comment in
window_settings_groups.py still passes.

GUI tests are Qt-only (the HelpWidget shape: one widget offscreen,
no MainWindow boot), gated the same way as the E-F202/E-F203/E-F205/
E-F206 suites (skipped on the sys interpreter, which has no
PySide6). Source pins run on both interpreters, including a negative
control proving the AST guard catches the exact split-line evasion
that fooled the old raw-text guard.
"""

import ast
import os
import pathlib
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False

HELP = ROOT / "gui" / "pf_settings_help.py"

GUARD_MODULES = (
    "pf_surfaces.py", "pf_surfaces_helpers.py", "pf_proof.py",
    "pf_settings_help.py", "pf_permissions.py",
    "pf_citizenship.py", "pf_privacy.py",
)
WS_FILES = (
    "window_settings.py", "window_settings_think.py",
    "window_settings_voice.py", "window_settings_groups.py",
    "window_settings_advbrain.py", "window_settings_advcards.py",
    "window_settings_appearance.py", "window_settings_privacy.py",
)

PERM_WANT = "Settings → Privacy & permissions → Permissions"
FACTS_WANT = "Settings → Privacy & permissions → Your stored facts"


def _help_bodies():
    """HELP_INDEX topic_id -> joined body text. The parser folds
    implicit string-literal concatenation, so ast.literal_eval on the
    body argument IS the rendered text."""
    tree = ast.parse(HELP.read_text(encoding="utf-8"))
    bodies = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "HELP_INDEX"
                for t in node.targets):
            for elt in node.value.elts:
                if isinstance(elt, ast.Call) and len(elt.args) >= 3:
                    bodies[ast.literal_eval(elt.args[0])] = \
                        ast.literal_eval(elt.args[2])
    return bodies


def _constants_with_old_name(source_text):
    """String constants containing 'More settings' in one source
    text — the AST guard. Implicit concatenation is folded by the
    parser, so the split-line evasion is visible here."""
    tree = ast.parse(source_text)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and "More settings" in n.value]


def _guard_paths():
    return [(ROOT / "gui" / m) for m in GUARD_MODULES] + \
        [(ROOT / "gui" / m) for m in WS_FILES]


class TestHelpCardCopy(unittest.TestCase):
    def test_permission_faq_names_privacy_permissions(self):
        body = _help_bodies()["why_refused_permission"]
        self.assertIn(PERM_WANT, body)
        self.assertNotIn("More settings", body)

    def test_stored_facts_faq_names_privacy_permissions(self):
        body = _help_bodies()["what_she_remembers"]
        self.assertIn(FACTS_WANT, body)
        self.assertNotIn("More settings", body)

    def test_no_more_settings_in_any_help_constant(self):
        # The strengthened guard, as a pin: no string constant in
        # any guarded module may say "More settings".
        for path in _guard_paths():
            hits = _constants_with_old_name(path.read_text(encoding="utf-8"))
            self.assertEqual(hits, [], str(path))

    def test_guard_catches_split_line_evasion(self):
        # Negative control: the exact split-line shape that fooled
        # the old raw-text guard. The raw check passes on it (the
        # hole); the AST check must catch it.
        evasion = ('x = ("Settings → More "\n'
                   '     "settings → Permissions to see each one")\n')
        self.assertNotIn("More settings", evasion)
        hits = _constants_with_old_name(evasion)
        self.assertEqual(len(hits), 1)
        self.assertIn("More settings", hits[0])


@unittest.skipUnless(_QT, "Qt-only: HelpWidget render")
class TestHelpWidgetRenders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import types
        pkg = types.ModuleType("voice_assistant")
        pkg.__path__ = [str(ROOT)]
        sys.modules.setdefault("voice_assistant", pkg)
        gui_pkg = types.ModuleType("voice_assistant.gui")
        gui_pkg.__path__ = [str(ROOT / "gui")]
        sys.modules.setdefault("voice_assistant.gui", gui_pkg)

    def _widget_labels(self, topics=None):
        from PySide6.QtWidgets import QApplication, QLabel
        from voice_assistant.gui.pf_settings_help import HelpWidget
        app = QApplication.instance() or QApplication([])
        widget = HelpWidget(topics=topics)
        widget.resize(680, 420)
        widget.show()
        app.processEvents()
        labels = [w.text() for w in widget.findChildren(QLabel)]
        widget.close()
        return labels

    def test_help_widget_renders_correct_paths(self):
        from voice_assistant.gui.pf_settings_help import HELP_INDEX
        topics = [t for t in HELP_INDEX
                  if t.topic_id in ("why_refused_permission",
                                    "what_she_remembers")]
        self.assertEqual(len(topics), 2)
        labels = self._widget_labels(topics=topics)
        self.assertTrue(any(PERM_WANT in t for t in labels), labels)
        self.assertTrue(any(FACTS_WANT in t for t in labels), labels)
        self.assertFalse(any("More settings" in t for t in labels), labels)

    def test_help_search_renders_correct_paths(self):
        # The search path renders the same fixed copy.
        from PySide6.QtWidgets import QApplication
        from voice_assistant.gui.pf_settings_help import HelpWidget
        app = QApplication.instance() or QApplication([])
        widget = HelpWidget()
        widget.resize(680, 420)
        widget.show()
        widget.search.setText("permission")
        app.processEvents()
        from PySide6.QtWidgets import QLabel
        labels = [w.text() for w in widget.findChildren(QLabel)]
        self.assertTrue(any(PERM_WANT in t for t in labels), labels)
        self.assertFalse(any("More settings" in t for t in labels), labels)
        widget.close()

    def test_help_widget_all_bodies_clean(self):
        labels = self._widget_labels()
        self.assertFalse(any("More settings" in t for t in labels), labels)


if __name__ == "__main__":
    unittest.main()
