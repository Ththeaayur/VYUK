"""UIS2 — home simplification (complexity doc slice 2 + CX-02).

Lab reality (stated, not dodged): GUI modules import frozen
``voice_assistant.*`` names and build real Qt widgets, so they
cannot be imported or driven here. This suite proves structure +
routing + handler wiring by AST over the real files. It does NOT
prove rendered appearance — field proof on real Windows is owed
(see the batch report): header shows a single status + persistent
privacy indicators while recording; every moved control reachable
and working from the overflow menu; idle shows one obvious start.

What S2 changed, in one paragraph: the header (``top`` layout in
``_build_assistant_page``) used to hold Export chat, the status
cluster, watch/capture pills, the mic-health dot, and Connect.
Export chat and the mic-health dot moved out; a "···" overflow
button took their slot. The status pill + glyph (one quiet system
status), the watch/capture privacy pills, and Connect stayed.
The mic dot object stays as a hidden render target so
``_refresh_mic_health`` keeps computing; its visible presence is
the overflow menu's live-words entry. Wave ribbon, dock waveform,
and the voice chip were NEVER header elements (stage/dock) and
are untouched — moving them would be a home-page redesign, out
of S2's header-area-only scope.
"""

import ast
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent / "gui"


def _tree(name):
    return ast.parse((TREE / name).read_text(encoding="utf-8"))


# INTENTIONAL (refactor P1 step 5): window_shell.py split verbatim
# into panel mixins; method names are unique across the shell
# modules, so the concatenated tree/text keeps every assertion.
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


def _shell_text():
    return "".join((TREE / n).read_text(encoding="utf-8")
                   for n in SHELL_FILES)


def _shell_tree():
    return ast.parse(_shell_text())


def _funcs(tree):
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            out[node.name] = node
    return out


def _func(tree, name):
    funcs = _funcs(tree)
    assert name in funcs, f"{name} not found"
    return funcs[name]


def _top_adds(func):
    """Attribute/local names added to the ``top`` header layout."""
    names = []
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "addWidget"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "top"
            and node.args
        ):
            arg = node.args[0]
            if isinstance(arg, ast.Attribute) and isinstance(
                arg.value, ast.Name
            ) and arg.value.id == "self":
                names.append(arg.attr)
            elif isinstance(arg, ast.Name):
                names.append(arg.id)
    return names


def _assigned_attrs(func):
    """Attribute names assigned to ``self`` inside a function.

    Phase D: the header's status/watch/capture pills are aliases
    (``self.status_pill = self.status_strip.status_label`` …) —
    the MainWindow attributes still exist and still point at the
    header's strip, so the header-membership check follows the
    aliases: cluster in header + alias in the builder function.
    """
    names = []
    for node in ast.walk(func):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.append(target.attr)
    return names


def _button_attrs(func):
    """self.<name> assigned a QPushButton(...) in this function."""
    out = set()
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "QPushButton"
        ):
            for target in node.targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    out.add(target.attr)
    return out


def _attr_calls(func, attr):
    return [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


class TestHeaderQuiet(unittest.TestCase):
    def setUp(self):
        self.func = _func(_shell_tree(),
                          "_build_assistant_page")
        self.top = _top_adds(self.func)
        # Phase D: the status cluster is a header child; its pills
        # are MainWindow aliases onto the strip's labels.
        self.assigned = _assigned_attrs(self.func)

    def test_moved_controls_out_of_header(self):
        self.assertNotIn("btn_export_chat", self.top,
                         "Export chat still in the header")
        self.assertNotIn("mic_health_dot", self.top,
                         "mic-health dot still in the header")

    def test_single_status_and_connection_stay(self):
        self.assertIn("status_strip", self.top,
                      "status cluster lost from header")
        for name in ("status_pill", "status_glyph_label"):
            self.assertIn(name, self.assigned, f"{name} lost from header")
        for name in ("btn_connect", "greeting"):
            self.assertIn(name, self.top, f"{name} lost from header")

    def test_header_buttons_are_overflow_plus_connect(self):
        buttons_in_top = _button_attrs(self.func) & set(self.top)
        self.assertEqual(
            buttons_in_top, {"btn_header_overflow", "btn_connect"},
            "header holds a button beyond overflow + Connect")

    def test_overflow_button_wired(self):
        for call in _attr_calls(self.func, "connect"):
            dump = ast.dump(call)
            if ("btn_header_overflow" in dump
                    and "_show_header_overflow_menu" in dump):
                return
        self.fail("overflow button never connected to its menu")

    def test_home_strip_not_dragged_into_header(self):
        # The home strip (date/next/briefing) is below the header;
        # S2 trims the header area only and leaves it alone.
        self.assertNotIn("btn_briefing", self.top)


class TestPrivacyPersistent(unittest.TestCase):
    def setUp(self):
        self.func = _func(_shell_tree(),
                          "_build_assistant_page")
        self.top = _top_adds(self.func)
        # Phase D: the privacy pills are MainWindow aliases onto
        # the header's StatusStrip cluster (see _assigned_attrs).
        self.assigned = _assigned_attrs(self.func)

    def test_privacy_pills_stay_in_header(self):
        self.assertIn("status_strip", self.top,
                      "status cluster lost from header")
        for name in ("watch_pill", "capture_pill"):
            self.assertIn(name, self.assigned,
                          f"{name} left the header — privacy "
                          "signals must never hide behind a menu")

    def test_privacy_pills_not_in_overflow_menu(self):
        menu = _func(_shell_tree(),
                     "_show_header_overflow_menu")
        dump = ast.dump(menu)
        self.assertNotIn("watch_pill", dump)
        self.assertNotIn("capture_pill", dump)

    def test_privacy_render_paths_survive(self):
        # Pills start hidden and are shown by these render paths
        # only while their sensor is active; S2 must not break
        # either path.
        # GUI split #11 batch 2 INTENTIONAL: _sync_watch_pill moved
        # verbatim to window_session_watch.py.
        _func(_tree("window_session_watch.py"), "_sync_watch_pill")
        # GUI split #10 batch 1 INTENTIONAL: _render_capture_indicator
        # moved verbatim to window_services_confirm.py.
        _func(_tree("window_services_confirm.py"), "_render_capture_indicator")


class TestOverflowMenu(unittest.TestCase):
    def setUp(self):
        self.menu = _func(_shell_tree(),
                          "_show_header_overflow_menu")

    def test_menu_builds_and_exports(self):
        dump = ast.dump(self.menu)
        self.assertIn("QMenu", dump)
        actions = [
            call for call in _attr_calls(self.menu, "addAction")
            if call.args
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == "Export chat"
        ]
        self.assertTrue(actions, "no 'Export chat' menu action")
        self.assertIn("_export_chat", dump,
                      "Export action never reaches _export_chat")
        connected = [
            call for call in _attr_calls(self.menu, "connect")
            if "triggered" in ast.dump(call.func)
        ]
        self.assertTrue(connected,
                        "Export action has no triggered connection")

    def test_menu_shows_live_mic_health_words(self):
        dump = ast.dump(self.menu)
        self.assertIn("mic_health_dot", dump)
        self.assertIn("_mic_health_key", dump)
        self.assertIn("Microphone health", dump)


class TestMovedControlsStillWork(unittest.TestCase):
    def test_export_handler_not_deleted(self):
        _func(_shell_tree(), "_export_chat")

    def test_button_attribute_gone_menu_action_present(self):
        # The QPushButton is gone; the menu action replaces it.
        # A leftover attribute would mean two Export entry points.
        dump = ast.dump(_shell_tree())
        self.assertNotIn("btn_export_chat", dump)

    def test_mic_render_target_survives(self):
        func = _func(_shell_tree(),
                     "_build_assistant_page")
        dump = ast.dump(func)
        self.assertIn("mic_health_dot", dump)
        _func(_tree("window_services_speech.py"),  # GUI split #10 batch 2 INTENTIONAL: _refresh_mic_health moved verbatim here.
                     "_refresh_mic_health")
        hidden = [
            call for call in _attr_calls(func, "setVisible")
            if isinstance(call.func.value, ast.Attribute)
            and call.func.value.attr == "mic_health_dot"
            and len(call.args) == 1
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value is False
        ]
        self.assertTrue(
            hidden,
            "mic dot must be a hidden render target, not a "
            "visible header widget and not deleted")


class TestIdleSingleStart(unittest.TestCase):
    def test_composer_is_the_start(self):
        func = _func(_shell_tree(),
                     "_build_assistant_page")
        dump = ast.dump(func)
        self.assertIn("input_cmd", dump, "composer input lost")
        self.assertIn("btn_send", dump, "composer Send lost")


class TestNonHeaderItemsUntouched(unittest.TestCase):
    def test_wave_and_voice_chip_never_in_header(self):
        func = _func(_shell_tree(),
                     "_build_assistant_page")
        top = _top_adds(func)
        self.assertNotIn("wave_ribbon", top)
        self.assertNotIn("voice_chip", top)

    def test_stage_voice_controls_intact(self):
        func = _func(_shell_tree(),
                     "_build_assistant_page")
        dump = ast.dump(func)
        self.assertIn("voice_chip", dump, "voice chip lost")
        self.assertIn("wave_ribbon", dump, "wave ribbon lost")


class TestOrbUntouched(unittest.TestCase):
    def test_hero_comment_and_widget_intact(self):
        text = _shell_text()
        # The comment wraps across lines; normalize before matching.
        flat = " ".join(text.replace("#", " ").split())
        self.assertIn(
            "The hero no longer folds away mid-conversation", flat,
            "hero comment lost — orb-centre-stage verdict pin")
        func = _func(_shell_tree(),
                     "_build_assistant_page")
        dump = ast.dump(func)
        self.assertIn("_HeroWidget", dump, "hero widget lost")

    def test_hero_never_hidden_or_resized(self):
        func = _func(_shell_tree(),
                     "_build_assistant_page")
        for call in ast.walk(func):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "setVisible"
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "hero"
            ):
                self.fail("hero visibility changed — orb verdict broken")
        hero_min = [
            call for call in ast.walk(func)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "setMinimumSize"
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "hero"
            and len(call.args) == 2
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value == 400
            and isinstance(call.args[1], ast.Constant)
            and call.args[1].value == 80
        ]
        self.assertTrue(hero_min, "hero minimum size changed")

    def test_stage_and_chat_floors_intact(self):
        # Chat pass (2026-11-17): the chat floor is 300 again —
        # the UI rethink Build 1 raise to 380 and its compact
        # tier are reverted to the v6.3 shape. The stage floor
        # (420) is untouched.
        func = _func(_shell_tree(),
                     "_build_assistant_page")
        floors = set()
        for call in ast.walk(func):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "setMinimumWidth"
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Constant)
            ):
                floors.add(call.args[0].value)
        self.assertIn(420, floors, "stage floor moved")
        self.assertIn(300, floors, "chat floor moved")


if __name__ == "__main__":
    unittest.main()
