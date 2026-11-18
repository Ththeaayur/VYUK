"""Command Deck Phase E — pages + copy pass + brain-mode truth.

Pure tests (no Qt import): the MODE-chip rule lives in the pure
§7 map module, and every page migration is pinned by reading the
migrated source — the tree's established pattern for GUI files no
test imports (uis2 proves structure by AST for the same reason).
Behaviour beyond source pins is proved by the Phase E renders and
the full run, not by pretending a source scan is a widget test.

What this suite pins:
* resolve_mode — the brain-mode source of truth (Phase E item:
  cloud/local mode claim authoritative end-to-end, never guessed).
* Hero copy — the wake-off idle sentence is scoped to the wake
  word, so it can never contradict the LISTENING pill (Phase E
  copy pass; the Phase D wart, resolved not carried).
* Page migrations — goalPill cyan wash + 2 px radius, Doctor
  severities on tokens, mic-health returns on tokens, Settings
  custom-hex default on the token, category/group palettes
  derived from tokens, ambience D2 cyan, token imports present.
"""

import re
import unittest
from pathlib import Path

from gui.design_system.session_states import resolve_mode

TREE = Path(__file__).resolve().parent.parent


def _src(rel):
    return (TREE / rel).read_text(encoding="utf-8")


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; the goal-pill pins live in the assistant module
# and the hex negative spans the whole shell, so read it concatenated.
SHELL_FILES = (
    "gui/window_shell.py",
    "gui/window_shell_frame.py",
    "gui/window_shell_tray.py",
    "gui/window_shell_settingsio.py",
    "gui/window_shell_memoryio.py",
    "gui/window_shell_activity.py",
    "gui/window_shell_onboarding.py",
    "gui/window_shell_sidebar.py",
    "gui/window_shell_assistant.py",
    "gui/window_shell_assistant_widgets.py",
    "gui/window_shell_briefing.py",
)


def _shell_source():
    return "".join(_src(n) for n in SHELL_FILES)


class ResolveModeTests(unittest.TestCase):
    def test_fallback_overrides_cloud_pick(self):
        self.assertEqual(resolve_mode(True, "gemini"), "LOCAL CORE")

    def test_fallback_overrides_local_pick(self):
        self.assertEqual(resolve_mode(True, "local"), "LOCAL CORE")

    def test_picked_local_is_local_core(self):
        self.assertEqual(resolve_mode(False, "local"), "LOCAL CORE")

    def test_picked_cloud_is_cloud(self):
        self.assertEqual(resolve_mode(False, "gemini"), "CLOUD")

    def test_unknown_engine_hides(self):
        self.assertEqual(resolve_mode(False, ""), "")
        self.assertEqual(resolve_mode(False, None), "")
        self.assertEqual(resolve_mode(False, "groq"), "")


class HeroCopyTests(unittest.TestCase):
    def test_wake_off_sentence_is_scoped_to_wake_word(self):
        # GUI split #11 batch 1 INTENTIONAL: _WAKE_OFF_IDLE_TEXT moved
        # verbatim to the suggestions module. Assertion unchanged.
        src = _src("gui/window_session_suggestions.py")
        self.assertIn("Not listening for “Hey VYUK”", src)

    def test_unscoped_sentence_is_gone(self):
        # The old sentence claimed total deafness ("Not listening
        # — wake word is off"), which contradicted a live
        # LISTENING pill. The scoped sentence keeps "wake word
        # is off" but never the unscoped claim.
        # GUI split #11 batch 1 INTENTIONAL: same move as above.
        src = _src("gui/window_session_suggestions.py")
        self.assertNotIn("Not listening — wake word is off", src)


class PageMigrationTests(unittest.TestCase):
    def test_goal_pill_cyan_wash_and_button_radius(self):
        src = _shell_source()
        region = src[src.index("goalPill"):]
        self.assertIn("with_alpha(ACCENT, 22)", region)
        self.assertIn("with_alpha(ACCENT, 70)", region)
        self.assertIn("RADIUS_BTN", region)
        self.assertNotIn("#7C8CFF", src)

    def test_doctor_severities_are_tokens(self):
        src = _src("gui/doctor_page.py")
        self.assertIn("_HEALTHY = OK", src)
        self.assertIn("_ATTENTION = AMBER", src)
        self.assertIn("_BROKEN = DANGER", src)

    def test_mic_health_returns_are_tokens(self):
        # GUI split #10 batch 1 INTENTIONAL: _mic_health_presentation
        # moved verbatim to window_services_confirm.py.
        src = _src("gui/window_services_confirm.py")
        self.assertIn('("no_stream", TEXT_MUTED', src)
        self.assertIn('("healthy", OK', src)
        self.assertIn('("dropping", DANGER', src)

    def test_settings_custom_hex_default_is_token(self):
        src = _src("gui/window_settings.py")
        self.assertNotIn("#35D6FF", src)
        # Multiline import block: ACCENT sits several lines
        # below the opening paren.
        self.assertRegex(src, r"from \.style import\s*\([^)]*ACCENT")

    def test_category_palettes_derive_from_tokens(self):
        # INTENTIONAL (refactor P1 step 5): category colours moved
        # verbatim to memory_web_graph.py. Assertion unchanged.
        mem = _src("gui/memory_web_graph.py")
        self.assertIn("lighten(ACCENT", mem)
        self.assertIn("ACCENT_2", mem)
        self.assertNotIn("#7C8CFF", mem)
        # GUI split #5 INTENTIONAL: galaxy palette moved verbatim to
        # skills_galaxy_data.py. Assertions unchanged.
        gal = _src("gui/skills_galaxy_data.py")
        self.assertIn("lighten(", gal)
        self.assertIn("ACCENT_2", gal)
        self.assertNotIn("#7C8CFF", gal)

    def test_ambience_d2_cyan(self):
        src = _src("gui/ambience.py")
        self.assertIn("LISTENING_HEX = ACCENT", src)
        self.assertIn("SPEAKING_HEX = ACCENT", src)
        self.assertIn("IDLE_HEX = TEXT_MUTED", src)

    def test_tab_component_rules_exist(self):
        # Render-found (Phase E): the Activity QTabWidget had
        # no rules and painted a white pane. The dark pane +
        # cyan selected tab live as a widget-scoped sheet in
        # window_pages.py on purpose — the tree pins NO tab-bar
        # rules in the global stylesheet (uis5 focus pin), so
        # style.py must NOT contain them either.
        # GUI split #8 INTENTIONAL: tab rules live in _build_activity_page, moved
        # verbatim to window_pages_activity.py. Assertion unchanged.
        pages = _src("gui/window_pages_activity.py")
        self.assertIn("QTabWidget::pane", pages)
        self.assertIn("QTabBar::tab:selected", pages)
        self.assertNotIn("QTabBar", _src("gui/style.py"))

    def test_nav_focus_differs_from_checked(self):
        # Render-found (Phase E): :focus used the same strong
        # wash as :checked, so the first-focusable Home row
        # looked selected on every page. Focus stays visible
        # but faint; checked keeps the strong wash. Blocks end
        # at a line-start brace (rule bodies contain ${...}
        # substitutions, so a plain "}" split truncates early).
        # INTENTIONAL (GUI split #6): the navBtn rules live in the QSS
        # template, which moved verbatim from gui/style.py to
        # gui/style_sheet.py. Assertions unchanged.
        style = _src("gui/style_sheet.py")
        focus = re.search(
            r"QPushButton#navBtn:focus \{(.*?)\n\}", style, re.DOTALL).group(1)
        checked = re.search(
            r"QPushButton#navBtn:checked \{(.*?)\n\}", style, re.DOTALL).group(1)
        self.assertIn("rgba(53, 214, 255, 14)", focus)
        self.assertIn("rgba(53, 214, 255, 30)", checked)
        self.assertNotEqual(focus, checked)

    def test_token_imports_present(self):
        senses = _src("gui/senses_page.py")
        self.assertRegex(
            senses, r"from \.style import[^\n]*TEXT, TEXT_MUTED, ACCENT, ACCENT_2")
        pulse = _src("gui/pulse_page.py")
        self.assertRegex(
            pulse, r"from \.style import[^\n]*OK, AMBER, DANGER, LINE_SUBTLE")
        # GUI split #9 INTENTIONAL: the ACCENT_2/AMBER style import lives
        # in the bubbles module now. Assertion unchanged.
        chat = _src("gui/window_chat_bubbles.py")
        self.assertRegex(chat, r"from \.style import[^\n]*ACCENT_2, AMBER")
        # GUI split #11 batch 2 INTENTIONAL: the session style import
        # split with the modules (ACCENT_2 -> suggestions, TEXT_MUTED
        # -> connection); no combined line exists anymore, so the
        # check follows the constants to their new homes.
        suggestions = _src("gui/window_session_suggestions.py")
        self.assertRegex(suggestions, r"from \.style import[^\n]*ACCENT_2")
        connection = _src("gui/window_session_connection.py")
        self.assertRegex(connection, r"from \.style import[^\n]*TEXT_MUTED")
        # GUI split #10 batch 1 INTENTIONAL: the style import now lives
        # in window_services_confirm.py (the facade remainder uses no
        # style constants directly). Assertion unchanged.
        services = _src("gui/window_services_confirm.py")
        self.assertRegex(
            services, r"from \.style import[^\n]*TEXT_MUTED, OK, AMBER, DANGER")


if __name__ == "__main__":
    unittest.main()
