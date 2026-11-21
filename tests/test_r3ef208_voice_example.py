"""Round-3 section E, E-F208: Voice card example ends mid-sentence.

The Voice card's voice line read `You can also say: "change her
voice to …"` — an example command that stops before the voice name.
The ellipsis was in the source copy (core/settings_search.py:171,
CARD_VOICE["Voice"]), not a wrap/clip artifact. Every other card's
voice line gives a complete example ("speak softer at night",
"slow the briefing down", "change the theme to Sunset").

The fix names a real offered voice: "change her voice to Aoede"
— Aoede is in config.available_voices (Aoede/Charon/Fenrir/Kore/
Puck), and the spoken voice change (app_settings live_voice)
matches the spoken name against exactly that offered list, so the
example is a command the product honours. Aoede is also not the
current voice (Charon), so the example asks for a real change.

GUI tests are Qt-only (the test_chat_ui WindowBase shape: the real
MainWindow offscreen), gated the same way as the E-F202/E-F203/
E-F205/E-F206/E-F207 suites (skipped on the sys interpreter, which
has no PySide6). Source pins run on both interpreters.
"""

import ast
import os
import pathlib
import re
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import PySide6  # noqa: F401 — availability probe only
    from tests.test_chat_ui import WindowBase
    _QT = True
except Exception:  # noqa: BLE001 — Qt unavailable, GUI tests skip
    _QT = False
    WindowBase = unittest.TestCase  # type: ignore[assignment]

SEARCH = ROOT / "core" / "settings_search.py"
SPEC = ROOT / "core" / "config_spec.py"


def _card_voice_table():
    """CARD_VOICE title -> line, folded exactly as Python sees it."""
    tree = ast.parse(SEARCH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "CARD_VOICE":
            value = node.value
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "CARD_VOICE":
                    value = node.value
        if value is not None and isinstance(value, ast.Dict):
            out = {}
            for key, val in zip(value.keys, value.values):
                if isinstance(key, ast.Constant) and isinstance(val, ast.Tuple):
                    line_node = val.elts[1]
                    if isinstance(line_node, ast.Constant):
                        out[key.value] = line_node.value
            return out
    raise AssertionError("CARD_VOICE not found")


def _offered_voices():
    """config.available_voices, parsed from the spec (no import)."""
    text = SPEC.read_text(encoding="utf-8")
    m = re.search(
        r"available_voices: list = field\(default_factory=lambda: \[(.*?)\]\)",
        text, re.S)
    if not m:
        raise AssertionError("available_voices not found")
    return re.findall(r'"([^"]+)"', m.group(1))


def _voice_named(line):
    m = re.search(r"change her voice to (\S+?)[”\"]", line)
    return m.group(1) if m else None


def _pump(app, secs=0.4):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


class TestVoiceLineCopy(unittest.TestCase):
    """Source pins (both interpreters): the Voice example is a
    complete sentence naming a real offered voice."""

    def test_voice_line_is_a_complete_sentence(self):
        line = _card_voice_table()["Voice"]
        self.assertNotIn("…", line)
        self.assertTrue(line.rstrip().endswith('”.'),
                        f"line trails off: {line!r}")
        self.assertIn("E-F208", SEARCH.read_text(encoding="utf-8"))

    def test_voice_line_names_an_offered_voice(self):
        line = _card_voice_table()["Voice"]
        named = _voice_named(line)
        self.assertIsNotNone(named, f"no voice named in: {line!r}")
        self.assertIn(named, _offered_voices())

    def test_sibling_lines_unchanged(self):
        table = _card_voice_table()
        self.assertIn("change the theme to Sunset", table["Appearance"])
        self.assertIn("speak softer at night", table["Whisper hours"])
        self.assertIn("slow the briefing down", table["Briefing voice"])

    def test_no_voice_line_trails_off(self):
        """Negative control over the whole table: no card's example
        ends in an ellipsis."""
        for title, line in _card_voice_table().items():
            self.assertNotIn("…", line, f"{title}: {line!r}")


@unittest.skipUnless(_QT, "Qt unavailable")
class _VoiceBase(WindowBase):
    """Real MainWindow offscreen, Settings page, Voice card."""

    def _show_voice_card(self):
        try:
            self.window._skip_boot_sequence()
        except Exception:  # noqa: BLE001 — overlay is best-effort
            pass
        _pump(self.app, 0.4)
        self.window._show_page(4)  # Settings page
        _pump(self.app, 0.6)
        try:
            self.window._refresh_settings_card_values()
        except Exception:  # noqa: BLE001 — refresh is best-effort
            pass
        _pump(self.app, 0.3)

    def _voice_label_text(self):
        labels = getattr(self.window, "_settings_card_voice_labels", {})
        label = labels.get("Voice")
        self.assertIsNotNone(label, "Voice card voice label missing")
        return label.text(), label


class TestVoiceCardRenders(_VoiceBase):
    """Qt tests: the real Voice card renders the complete line."""

    def test_voice_card_renders_the_complete_line(self):
        self._show_voice_card()
        rendered, _label = self._voice_label_text()
        self.assertEqual(rendered, _card_voice_table()["Voice"])
        self.assertNotIn("…", rendered)

    def test_search_renders_the_complete_line(self):
        """Second surface (E-F204 lesson): Settings search for
        "voice" keeps the card shown with the same complete line."""
        from PySide6.QtWidgets import QLineEdit
        self._show_voice_card()
        search = None
        for edit in self.window.findChildren(QLineEdit):
            if "what you want to change" in (edit.placeholderText() or ""):
                search = edit
                break
        self.assertIsNotNone(search, "Settings search box not found")
        search.setText("voice")
        _pump(self.app, 0.6)
        try:
            self.window._refresh_settings_card_values()
        except Exception:  # noqa: BLE001 — refresh is best-effort
            pass
        _pump(self.app, 0.3)
        rendered, label = self._voice_label_text()
        self.assertTrue(label.isVisible())
        self.assertEqual(rendered, _card_voice_table()["Voice"])
        self.assertNotIn("…", rendered)

    def test_no_rendered_voice_line_trails_off(self):
        self._show_voice_card()
        labels = getattr(self.window, "_settings_card_voice_labels", {})
        self.assertTrue(labels, "no voice labels on the Settings page")
        for title, label in labels.items():
            self.assertNotIn("…", label.text(), f"{title}: {label.text()!r}")
