"""UI a11y foundations slice (2026-10-30) — bubble focus path,
accessible names/descriptions, main-window Tab order, keyboard
pass, screen-reader readiness.

LAB HONESTY NOTE: GUI modules import the frozen
``voice_assistant.*`` package names, so they cannot be imported
plainly in this lab. The live-widget tests below import the REAL
gui/window_widgets.py through stub voice_assistant.core.voice.audio /
.brain modules (the audit2s1 precedent — the stubs are never
called, attribute stubs suffice), plus exec'd real function
bodies for the Tab wiring (the uis6 precedent). Shell/settings/
onboarding coverage is proven by AST over the real files (the
names are literal call sites) plus the effective-name probe for
the mechanism. This proves structure + mechanism, NOT rendered
appearance or Narrator behaviour: the field pass below is still
owed.

NARRATOR FIELD CHECKLIST (real Windows, in order):
1. Start Narrator (Ctrl+Win+Enter), Tab from the sidebar: hear
   Home, Activity, Memory, System Pulse, Senses, Doctor,
   Settings in that order, each announced as "Show the X page".
2. Tab into the stage: voice chip, briefing, goal stop, speech
   pause/skip, pace buttons — each named, pace buttons announce
   their speed.
3. Tab into the chat: each message announces "Message from
   VYUK" / "Message from You", then its Copy / Read aloud
   actions; Tab order follows arrival order, then the composer.
4. Composer announces "Message to VYUK"; Send, mic, screen
   buttons announce their names, not "button" alone.
5. Header: overflow announces "More conversation actions",
   Connect announces its description; titlebar pill + window
   buttons announce Minimize / Maximize / Close window.
6. Settings: every input, combo, slider and time edit announces
   its name (search, keys, voice, mic, volumes, times, models).
7. Palette (Ctrl+K): input announces "Command palette search",
   list announces "Matching commands", Esc closes it.
8. First-open dialog: Yes/No announce their full choice text,
   No is the default; onboarding key + mic controls announce.
9. Keyboard-only pass: every action above reachable and
   operable with no mouse, with a visible focus border at each
   stop (buttons) or a visible actions row (bubbles).
"""

import ast
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = ROOT / "gui"


def _read(name):
    return (GUI / name).read_text(encoding="utf-8")


# INTENTIONAL (refactor P1 step 5): window_settings.py split verbatim
# into family modules; the settings a11y pins now span them.
SETTINGS_FILES = (
    "window_settings.py",
    "window_settings_think.py",
    "window_settings_voice.py",
    "window_settings_groups.py",
    "window_settings_advbrain.py",
    "window_settings_advcards.py",
    "window_settings_appearance.py",
    "window_settings_privacy.py",
)


def _settings_source():
    return "".join(_read(n) for n in SETTINGS_FILES)


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; the shell a11y pins span frame + assistant, so
# read the shell surface concatenated. Assertions unchanged.
SHELL_FILES = (
    "window_shell.py",
    "window_shell_frame.py",
    "window_shell_tray.py",
    "window_shell_settingsio.py",
    "window_shell_memoryio.py",
    "window_shell_activity.py",
    "window_shell_onboarding.py",
    "window_shell_sidebar.py",
    "window_shell_assistant.py",
    "window_shell_assistant_widgets.py",
    "window_shell_briefing.py",
)


def _shell_source():
    return "".join(_read(n) for n in SHELL_FILES)


def _func_segment(src, class_name, fn_name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and \
                        item.name == fn_name:
                    return ast.get_source_segment(src, item) or ""
    raise AssertionError(f"{class_name}.{fn_name} not found")


def _module_fn_segment(src, fn_name):
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"{fn_name} not found")


# ---------------------------------------------------------------- Qt setup
try:
    from PySide6.QtWidgets import QApplication  # noqa: F401
    _QT = True
except ImportError:
    _QT = False

_APP = None

# L3: snapshot slot for the voice_assistant.* sys.modules entries
# setUpModule captures before installing stubs (None = setUpModule
# never ran, so tearDownModule must not touch sys.modules).
_VA_SNAPSHOT = None


def setUpModule():
    global _APP, _VA_SNAPSHOT
    # L3: snapshot the pre-existing voice_assistant.* entries
    # BEFORE installing stubs, so tearDownModule restores exactly
    # what was there instead of deleting everything — the old
    # delete-all broke chatfix when uia11y ran first (it wiped
    # chatfix's cached real w9_wiring, leaving its error-ladder
    # tests with ladder None / missing attribute). Same
    # snapshot/restore discipline as the budget guest rule.
    _VA_SNAPSHOT = {k: v for k, v in sys.modules.items()
                    if k == "voice_assistant"
                    or k.startswith("voice_assistant.")}
    if not _QT:
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication as _QA
    if _QA.instance() is None:
        _APP = _QA(sys.argv)
    tree = str(ROOT)
    pkg = types.ModuleType("voice_assistant")
    pkg.__path__ = [tree]
    core = types.ModuleType("voice_assistant.core")
    core.__path__ = [os.path.join(tree, "core")]
    audio = types.ModuleType("voice_assistant.core.voice.audio")  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
    audio.audio_manager = types.SimpleNamespace()
    brain = types.ModuleType("voice_assistant.core.brain.brain")  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
    brain.live_session = types.SimpleNamespace()
    w9 = types.ModuleType("voice_assistant.core.w9_wiring")
    w9.note_chat_message = lambda *a, **k: None
    for name, mod in (("voice_assistant", pkg),
                      ("voice_assistant.core", core),
                      ("voice_assistant.core.voice.audio", audio),  # Phase 3 V1a-1 INTENTIONAL: stub follows audio move
                      ("voice_assistant.core.brain.brain", brain),  # Phase 3 B2h: brain hub moved to core/brain/brain.py INTENTIONAL: stub follows hub move
                      ("voice_assistant.core.w9_wiring", w9)):
        sys.modules[name] = mod


def tearDownModule():
    global _VA_SNAPSHOT
    if _VA_SNAPSHOT is None:
        return
    for key in list(sys.modules):
        if key == "voice_assistant" or key.startswith("voice_assistant."):
            del sys.modules[key]
    sys.modules.update(_VA_SNAPSHOT)
    _VA_SNAPSHOT = None


def _eff_name(widget):
    """What a screen reader actually announces for this widget:
    the QAccessible effective name (explicit name, else derived
    text for buttons/checkboxes, else empty)."""
    from PySide6.QtGui import QAccessible
    iface = QAccessible.queryAccessibleInterface(widget)
    if iface is None:
        return ""
    return iface.text(QAccessible.Text.Name)


def _eff_desc(widget):
    from PySide6.QtGui import QAccessible
    iface = QAccessible.queryAccessibleInterface(widget)
    if iface is None:
        return ""
    return iface.text(QAccessible.Text.Description)


def _pump(times=3):
    from PySide6.QtWidgets import QApplication
    for _ in range(times):
        QApplication.processEvents()


def _tab_chain(start, stop):
    """Tab-reachable widgets after start, up to stop inclusive.
    nextInFocusChain also lists ClickFocus-only internals (e.g.
    a QTextEdit's viewport) that Tab never lands on — skipped."""
    from PySide6.QtCore import Qt
    out = []
    cur = start
    for _ in range(60):
        cur = cur.nextInFocusChain()
        if cur.focusPolicy() & Qt.FocusPolicy.TabFocus:
            out.append(cur)
        if cur is stop:
            break
    return out


# ------------------------------------------------------- live widget tests
class TestBubbleFocusPath(unittest.TestCase):
    def setUp(self):
        if not _QT:
            self.skipTest("PySide6 unavailable in this interpreter")
        from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton
        from gui.window_widgets import ChatBubble
        self.ChatBubble = ChatBubble
        self.parent = QWidget()
        self.parent.setLayout(QVBoxLayout())
        self.outside = QPushButton("Outside")
        self.parent.layout().addWidget(self.outside)
        self.bubble_nova = ChatBubble("VYUK", "Hello there.", False)
        self.parent.layout().addWidget(self.bubble_nova)
        self.bubble_user = ChatBubble("You", "Hi.", True)
        self.parent.layout().addWidget(self.bubble_user)
        self.parent.show()
        _pump()

    def tearDown(self):
        self.parent.close()

    def test_bubble_and_action_names(self):
        # Chat UI build (2026-11-16): the hover row is Copy,
        # Regenerate (VYUK's) / Edit (user's), overflow — Read
        # aloud moved behind the overflow menu, so no speak
        # button exists on either bubble any more.
        self.assertEqual(_eff_name(self.bubble_nova), "Message from VYUK")
        self.assertEqual(_eff_name(self.bubble_nova._btn_copy),
                         "Copy message")
        self.assertEqual(_eff_desc(self.bubble_nova._btn_copy),
                         "Copy this message to the clipboard")
        self.assertEqual(_eff_name(self.bubble_nova._btn_regen),
                         "Regenerate answer")
        self.assertEqual(_eff_desc(self.bubble_nova._btn_regen),
                         "Ask for a different answer to the same message")
        self.assertIsNone(self.bubble_nova._btn_edit)
        self.assertEqual(_eff_name(self.bubble_user), "Message from You")
        self.assertIsNone(self.bubble_user._btn_regen)
        self.assertEqual(_eff_name(self.bubble_user._btn_edit),
                         "Edit message")
        self.assertEqual(_eff_desc(self.bubble_user._btn_edit),
                         "Fix this message and get a new answer")
        self.assertEqual(_eff_name(self.bubble_user._btn_copy),
                         "Copy message")
        self.assertFalse(hasattr(self.bubble_nova, "_btn_speak"))
        self.assertFalse(hasattr(self.bubble_user, "_btn_speak"))

    def test_focus_shows_row_and_leaving_hides(self):
        row = self.bubble_nova._actions_row
        self.assertFalse(row.isVisible())
        self.bubble_nova.setFocus()
        _pump()
        self.assertTrue(row.isVisible())
        self.outside.setFocus()
        _pump()
        self.assertFalse(row.isVisible())

    def test_focus_into_row_keeps_row_visible(self):
        row = self.bubble_nova._actions_row
        self.bubble_nova.setFocus()
        _pump()
        self.assertTrue(row.isVisible())
        self.bubble_nova._btn_copy.setFocus()
        _pump()
        self.assertTrue(row.isVisible())
        # Chat UI build (2026-11-16): the speak button is gone —
        # Read aloud is a menu action now. Focus moves through
        # the row's Regenerate button instead.
        self.bubble_nova._btn_regen.setFocus()
        _pump()
        self.assertTrue(row.isVisible())
        self.outside.setFocus()
        _pump()
        self.assertFalse(row.isVisible())

    def test_mouse_leave_with_keyboard_focus_keeps_row(self):
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
        row = self.bubble_nova._actions_row
        self.bubble_nova.setFocus()
        self.bubble_nova._btn_copy.setFocus()
        _pump()
        self.assertTrue(row.isVisible())
        QApplication.sendEvent(self.bubble_nova,
                               QEvent(QEvent.Type.Leave))
        _pump()
        self.assertTrue(row.isVisible())

    def test_tab_tail(self):
        # Chat UI build (2026-11-16): the tail is the row's last
        # button in row order (the overflow) — _tab_tail no longer
        # scans visibility, because the chain is built while the
        # row is hidden and scanning it stranded Regenerate /
        # overflow after the composer. Focus first so the row is
        # showing, the state a keyboard user actually tabs from.
        self.bubble_nova.setFocus()
        _pump()
        self.assertIs(self.bubble_nova._tab_tail(),
                      self.bubble_nova._btn_overflow)
        self.bubble_user.setFocus()
        _pump()
        self.assertIs(self.bubble_user._tab_tail(),
                      self.bubble_user._btn_overflow)

    def test_codeblock_copy_description(self):
        from PySide6.QtWidgets import QPushButton
        from gui.window_widgets import _CodeBlock
        block = _CodeBlock("x = 1")
        btns = [c for c in block.findChildren(QPushButton)
                if c.text() == "Copy"]
        self.assertEqual(len(btns), 1)
        self.assertEqual(_eff_desc(btns[0]), "Copy this code block")


class TestPaletteNamesAndEscape(unittest.TestCase):
    def setUp(self):
        if not _QT:
            self.skipTest("PySide6 unavailable in this interpreter")
        from PySide6.QtWidgets import QWidget
        from gui.window_widgets import CommandPalette
        self.stub = QWidget()
        self.pal = CommandPalette(self.stub)
        self.stub.show()
        _pump()

    def tearDown(self):
        self.pal.close()
        self.stub.close()

    def test_palette_names(self):
        self.assertEqual(_eff_name(self.pal.input),
                         "Command palette search")
        self.assertEqual(_eff_name(self.pal.list), "Matching commands")
        self.assertEqual(self.pal.windowTitle(), "Command palette")

    def test_escape_closes_palette(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.pal.show()
        _pump()
        self.assertTrue(self.pal.isVisible())
        QTest.keyClick(self.pal, Qt.Key.Key_Escape)
        _pump()
        self.assertFalse(self.pal.isVisible())


class TestBubbleTabWiring(unittest.TestCase):
    """Real _add_bubble body, exec'd with a fake window shell:
    proves the Tab chain bubbles follow in arrival order."""

    def setUp(self):
        if not _QT:
            self.skipTest("PySide6 unavailable in this interpreter")
        import types as _types
        from PySide6.QtWidgets import (QHBoxLayout, QPushButton,
                                       QTextEdit, QVBoxLayout, QWidget)
        from gui.window_widgets import ChatBubble
        self.parent = QWidget()
        self.parent.setLayout(QVBoxLayout())
        self.anchor = QPushButton("Anchor")
        self.parent.layout().addWidget(self.anchor)
        self.input_cmd = QTextEdit()
        self.parent.layout().addWidget(self.input_cmd)
        self.fake = _types.SimpleNamespace(
            chat_layout=self.parent.layout(),
            _last_bubble=None,
            _last_bubble_user=None,
            _group_break=True,
            _chat_tab_anchor=self.anchor,
            input_cmd=self.input_cmd,
            _mark_chat_has_messages=lambda: None,
            _maybe_add_day_divider=lambda: None,
            _fit_chat_item=lambda b: None,
            # Chat UI build (2026-11-16): _fade_in gained a
            # target_opacity kwarg (dimmed bubbles fade to 0.6).
            _fade_in=lambda b, animate=True, target_opacity=1.0: None,
            _scroll_chat_to_bottom=lambda: None,
        )
        # GUI split #9 INTENTIONAL: _add_bubble lives in the bubbles module
        # now, under ChatBubblesMixin. Segment exec unchanged.
        src = _read("window_chat_bubbles.py")
        seg = _func_segment(src, "ChatBubblesMixin", "_add_bubble")
        ns = {"ChatBubble": ChatBubble, "QHBoxLayout": QHBoxLayout}
        exec(compile(ast.parse(seg), "<_add_bubble>", "exec"), ns)
        self.add_bubble = ns["_add_bubble"]
        self.parent.show()
        _pump()

    def tearDown(self):
        self.parent.close()

    def test_chain_follows_arrival_order(self):
        self.add_bubble(self.fake, "VYUK", "Hello.", False)
        b1 = self.fake._last_bubble
        self.add_bubble(self.fake, "You", "Hi.", True)
        b2 = self.fake._last_bubble
        chain = _tab_chain(self.anchor, self.input_cmd)
        # Chat UI build (2026-11-16): the hover row is Copy,
        # Regenerate/Edit, overflow — the chain walks each
        # bubble's row in order before the next bubble.
        self.assertEqual(
            chain,
            [b1, b1._btn_copy, b1._btn_regen, b1._btn_overflow,
             b2, b2._btn_copy, b2._btn_edit, b2._btn_overflow,
             self.input_cmd])


class TestStaticTabOrder(unittest.TestCase):
    """Real _apply_main_tab_order body, exec'd with a fake shell:
    proves the static chain order, not just its presence."""

    def setUp(self):
        if not _QT:
            self.skipTest("PySide6 unavailable in this interpreter")
        import types as _types
        from PySide6.QtWidgets import (QPushButton, QTextEdit, QWidget,
                                       QVBoxLayout)
        self.parent = QWidget()
        self.parent.setLayout(QVBoxLayout())
        mk = lambda t: QPushButton(t, self.parent)
        self.fake = _types.SimpleNamespace(
            nav_buttons=[mk(f"Nav{i}") for i in range(8)],
            voice_chip=mk("Voice"),
            btn_briefing=mk("Briefing"),
            btn_goal_stop=mk("Stop"),
            btn_speech_pause=mk("Pause"),
            btn_speech_skip=mk("Skip"),
            pace_buttons={0.9: mk("0.9"), 1.0: mk("1.0"),
                          1.1: mk("1.1")},
            input_cmd=QTextEdit(self.parent),
            btn_send=mk("Send"),
            btn_mic=mk("Mic"),
            btn_screen=mk("Screen"),
            btn_find_prev=mk("Prev"),
            btn_find_next=mk("Next"),
            btn_find_close=mk("CloseFind"),
            _btn_new_msgs=mk("New"),
            btn_header_overflow=mk("Overflow"),
            btn_connect=mk("Connect"),
            btn_cmd_pill=mk("Pill"),
            btn_win_min=mk("Min"),
            btn_win_max=mk("Max"),
            btn_win_close=mk("Close"),
        )
        for w in (list(self.fake.nav_buttons)
                  + [self.fake.voice_chip, self.fake.btn_briefing,
                     self.fake.btn_goal_stop, self.fake.btn_speech_pause,
                     self.fake.btn_speech_skip]
                  + list(self.fake.pace_buttons.values())
                  + [self.fake.input_cmd, self.fake.btn_send,
                     self.fake.btn_mic, self.fake.btn_screen,
                     self.fake.btn_find_prev, self.fake.btn_find_next,
                     self.fake.btn_find_close, self.fake._btn_new_msgs,
                     self.fake.btn_header_overflow, self.fake.btn_connect,
                     self.fake.btn_cmd_pill, self.fake.btn_win_min,
                     self.fake.btn_win_max, self.fake.btn_win_close]):
            self.parent.layout().addWidget(w)
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _read("window_shell_sidebar.py")
        seg = _func_segment(src, "ShellSidebarMixin", "_apply_main_tab_order")
        ns = {"QWidget": QWidget}
        exec(compile(ast.parse(seg), "<_apply_main_tab_order>",
                     "exec"), ns)
        ns["_apply_main_tab_order"](self.fake)
        self.parent.show()
        _pump()

    def tearDown(self):
        self.parent.close()

    def test_chain_order(self):
        f = self.fake
        expected = (list(f.nav_buttons)
                    + [f.voice_chip, f.btn_briefing, f.btn_goal_stop,
                       f.btn_speech_pause, f.btn_speech_skip,
                       f.pace_buttons[0.9], f.pace_buttons[1.0],
                       f.pace_buttons[1.1], f.input_cmd, f.btn_send,
                       f.btn_mic, f.btn_screen, f.btn_find_prev,
                       f.btn_find_next, f.btn_find_close,
                       f._btn_new_msgs, f.btn_header_overflow,
                       f.btn_connect, f.btn_cmd_pill, f.btn_win_min,
                       f.btn_win_max, f.btn_win_close])
        self.assertIs(f._chat_tab_anchor, f.pace_buttons[1.1])
        chain = _tab_chain(expected[0], expected[-1])
        self.assertEqual(chain, expected[1:])


# ------------------------------------------------------------- AST pins
class TestShellNamesPresent(unittest.TestCase):
    def test_shell_names_and_descriptions(self):
        # INTENTIONAL (refactor P1 step 5): see _shell_source above.
        src = _shell_source()
        for pin in (
                'setAccessibleName("Minimize window")',
                'setAccessibleName("Maximize window")',
                'setAccessibleName("Close window")',
                'setAccessibleName(\n            "More conversation actions")',
                'setAccessibleName("Jump to new messages")',
                'setAccessibleName("Find previous match")',
                'setAccessibleName("Find next match")',
                'setAccessibleName("Close find bar")',
                'setAccessibleName("Message to VYUK")',
                'setAccessibleName("Send message")',
                'setAccessibleName("Hold to talk")',
                'setAccessibleName("Share screen")',
                # Chat pass (2026-11-17): the description is the
                # v6.3 form again — action only, no group suffix
                # (Build 3's group suffix is gone with the section
                # labels; proven at backup_release_v630_pre
                # gui/window_shell_sidebar.py:314).
                'f"Show the {label} page"',
                '"Connect to the voice assistant, or disconnect when "',
                '"Turn the wake word on or off"',
                '"Stop the running goal or plan"',
                'f"Set speech pace to {_label}"',
                '"Mute or unmute her spoken replies"',
                '"Type a message; Enter sends it, Up arrow recalls "',
                '"Send the typed message. Enter works too."',
                '"Press and hold to talk; release to send. Holding "',
                ):
            self.assertIn(pin, src, f"missing shell a11y pin: {pin}")

    def test_tab_chain_method_order(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        src = _read("window_shell_sidebar.py")
        seg = _func_segment(src, "ShellSidebarMixin", "_apply_main_tab_order")
        order = ("nav_buttons", "voice_chip", "btn_briefing",
                 "btn_goal_stop", "btn_speech_pause", "btn_speech_skip",
                 "pace_buttons", "input_cmd", "btn_send", "btn_mic",
                 "btn_screen", "btn_find_prev", "btn_find_next",
                 "btn_find_close", "_btn_new_msgs",
                 "btn_header_overflow", "btn_connect", "btn_cmd_pill",
                 "btn_win_min", "btn_win_max", "btn_win_close")
        idx = -1
        for name in order:
            at = seg.find(f'"{name}"')
            if at < 0:
                at = seg.find(name)
            self.assertGreater(at, idx, f"chain order broken at {name}")
            idx = at

    def test_bubble_wiring_present(self):
        # GUI split #9 INTENTIONAL: bubble wiring lives in the bubbles
        # module now. Assertions unchanged.
        # Chat UI build (2026-11-16): the bubble-to-composer link
        # is chained inline through the row's buttons now
        # (bubble._action_buttons, in row order) instead of one
        # bubble._tab_tail() call — the tail only exists once a
        # foot row appears, and those re-chain through
        # _chain_bubble_tab (window_chat_stream.py).
        src = _read("window_chat_bubbles.py")
        self.assertIn("prev._tab_tail()", src)
        self.assertIn("_chat_tab_anchor", src)
        self.assertIn("bubble._action_buttons", src)


class TestSettingsOnboardingNames(unittest.TestCase):
    def test_settings_input_names(self):
        # INTENTIONAL (refactor P1 step 5): see _settings_source above.
        src = _settings_source()
        for pin in (
                'setAccessibleName("Search settings")',
                'setAccessibleName("Cloud brain provider")',
                'setAccessibleName("Gemini API key")',
                'setAccessibleName("Groq API key")',
                'setAccessibleName("Voice")',
                'setAccessibleName("Microphone")',
                'setAccessibleName("Whisper hours start time")',
                'setAccessibleName("Whisper hours end time")',
                'setAccessibleName("Wake phrase")',
                'setAccessibleName("Interface sounds volume")',
                'setAccessibleName("Morning briefing time")',
                'setAccessibleName("Browser debug port")',
                'setAccessibleName("Ollama model")',
                'setAccessibleName("Language")',
                'setAccessibleName("Kokoro voice")',
                'setAccessibleName("Custom Kokoro voice")',
                'setAccessibleName("Kokoro blend voice")',
                'setAccessibleName("Kokoro blend ratio")',
                'setAccessibleName("Kokoro speech speed")',
                'setAccessibleName("Local text-to-speech voice")',
                'setAccessibleName("Local model")',
                ):
            self.assertIn(pin, src, f"missing settings a11y pin: {pin}")

    def test_onboarding_names(self):
        # INTENTIONAL (GUI split #13 batch 1): both a11y pins moved
        # verbatim with their pages (_page_signin, _page_mic) to the
        # pages mixin. Assertion unchanged. (Round-3 OAuth build:
        # the key page's "Gemini API key" pin is gone with the
        # page — the sign-in button's "Continue with Google" pin
        # takes its place.)
        src = _read("onboarding_pages.py")
        self.assertIn('setAccessibleName("Continue with Google")', src)
        self.assertIn('setAccessibleName("Microphone")', src)


class TestBubbleMechanismPresent(unittest.TestCase):
    def test_bubble_focus_mechanism(self):
        # INTENTIONAL (refactor P1 step 5): window_widgets.py split into
        # family modules; the pins now live in bubble (ChatBubble),
        # palette (CommandPalette) and chatbits (_CodeBlock). Concatenated
        # source keeps every pin assertion unchanged.
        src = (_read("window_widgets_bubble.py")
               + _read("window_widgets_palette.py")
               + _read("window_widgets_chatbits.py"))
        for pin in ("setFocusPolicy(Qt.FocusPolicy.StrongFocus)",
                    "def focusInEvent",
                    "def focusOutEvent",
                    "def _maybe_hide_actions",
                    "def _tab_tail",
                    "installEventFilter(self)",
                    'setAccessibleName(f"Message from {who}")',
                    # INTENTIONAL (chat UI build, 2026-11-16): the
                    # hover row is built by _make_action_button
                    # now — the labels ride as its arguments and
                    # the helper sets them (btn.setAccessibleName
                    # (acc_name)); Read aloud moved behind the one
                    # overflow menu, as a menu action.
                    '"Copy message"',
                    '"Read aloud"',
                    'btn.setAccessibleName(acc_name)',
                    'setAccessibleName("Command palette search")',
                    'setAccessibleName("Matching commands")',
                    'setAccessibleDescription("Copy this code block")'):
            self.assertIn(pin, src, f"missing bubble a11y pin: {pin}")

    def test_bubble_focus_style(self):
        # INTENTIONAL (GUI split #6): the bubble focus rules live in
        # the QSS template, which moved verbatim from gui/style.py to
        # gui/style_sheet.py. Assertion unchanged.
        src = _read("style_sheet.py")
        self.assertIn("QFrame#bubbleUser:focus, QFrame#bubbleNova:focus",
                      src)


class TestKeyboardPassPins(unittest.TestCase):
    def test_ctrl_k_and_del_shortcuts(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        self.assertIn('QKeySequence("Ctrl+K")', _read("window_shell_frame.py"))
        # GUI split #8 INTENTIONAL: Del shortcut lives in _build_reminders_page,
        # moved verbatim to window_pages_activity.py. Assertion unchanged.
        self.assertIn('QKeySequence("Del")', _read("window_pages_activity.py"))

    def test_firstopen_no_default(self):
        src = _read("firstopen_prompt.py")
        self.assertIn("self.btn_no.setDefault(True)", src)
        # Yes/No announce their full choice text by derivation.
        from core import firstopen_optin as optin_mod
        choices = optin_mod.first_open_prompt()["choices"]
        self.assertEqual(len(choices), 2)
        self.assertTrue(all(len(c) > 3 for c in choices))

    def test_onboarding_space_return(self):
        # INTENTIONAL (GUI split #13 batch 1): Space/Return handling
        # moved verbatim with _ChoiceCard.keyPressEvent to widgets.
        # Assertion unchanged.
        src = _read("onboarding_widgets.py")
        self.assertIn("Qt.Key.Key_Space", src)
        self.assertIn("Qt.Key.Key_Return", src)

    def test_details_and_chips_are_real_buttons(self):
        # S3 task rows render in gui/task_state.py (render_task),
        # not window_memory.py — the Details control is a real
        # QPushButton there, and chips are QPushButtons in shell.
        task_state = _read("task_state.py")
        self.assertIn("QPushButton", task_state)
        self.assertIn('details_btn = QPushButton("Details")', task_state)
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        shell = _read("window_shell_frame.py")
        self.assertIn("QPushButton", shell)


if __name__ == "__main__":
    unittest.main()
