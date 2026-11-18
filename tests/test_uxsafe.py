"""UX safe-items batch (2026-10-30): About copy, motion gate (2 sites),
Reminders scroll, onboarding resizable/scrollable.

LAB HONESTY NOTE: the GUI modules import the frozen ``voice_assistant.*``
package names and build real Qt widgets, so they cannot be imported or
driven in this lab (PySide6 Essentials only, no QtWebEngine, no real
GUI driving). This suite therefore verifies STRUCTURE + GATE LOGIC by
AST over the real files, and executes the one pure seam (the About
hint function, extracted from the file and exec'd in isolation) for
real. It proves the wiring and the logic, NOT rendered appearance:
field proof on real Windows is still owed for all four items.
"""

import ast
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parent.parent
GUI = TREE / "gui"


def _source(name):
    return (GUI / name).read_text(encoding="utf-8")


def _tree(name):
    return ast.parse(_source(name))


def _func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _method(tree, class_name, method_name):
    """The __init__ (etc.) of one named class — the file's first
    __init__ is _ChoiceCard's, not the dialog's."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and item.name == method_name:
                    return item
    raise AssertionError(f"{class_name}.{method_name} not found")


def _opacity_calls(func_node, value):
    """setWindowOpacity calls with exactly this float constant arg
    (ast.dump renders 1.0 as Constant(value=1.0), so a plain
    substring search for 'setWindowOpacity(1.0)' never matches)."""
    out = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call) \
                and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "setWindowOpacity" \
                and len(node.args) == 1 \
                and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == value:
            out.append(node)
    return out


def _calls(func_node):
    return [n for n in ast.walk(func_node) if isinstance(n, ast.Call)]


def _attr_calls(func_node, attr):
    return [c for c in _calls(func_node)
            if isinstance(c.func, ast.Attribute) and c.func.attr == attr]


def _load_about_hint():
    """Exec ONLY the pure _about_hint_text def from
    window_settings_advcards.py (INTENTIONAL, refactor P1 step 5: the
    def moved verbatim from window_settings.py).

    The module itself cannot be imported in the lab (frozen-package
    imports + Qt), but this function is pure string logic, so the
    exact def node from the file is compiled and run in isolation.
    """
    tree = _tree("window_settings_advcards.py")
    node = _func(tree, "_about_hint_text")
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {}
    exec(compile(module, "window_settings.py", "exec"), namespace)
    return namespace["_about_hint_text"]


class TestAboutCopy(unittest.TestCase):
    def test_no_hardcoded_494_anywhere_in_settings(self):
        src = _source("window_settings.py")
        self.assertNotIn("494", src)

    def test_gods_eye_removed(self):
        # INTENTIONAL (world-view removal, 2026-11-05): the feature
        # is gone, so its name must be gone from the About card too.
        # This assertion is the one remaining "God's Eye" string in
        # the tree outside historical records: an absence pin, not
        # a live reference.
        # INTENTIONAL (refactor P1 step 5): the About card moved
        # verbatim to window_settings_advcards.py. Assertion unchanged.
        self.assertNotIn("God's Eye", _source("window_settings_advcards.py"))

    def test_builder_wires_live_count_and_engine(self):
        # INTENTIONAL (refactor P1 step 5): the About card moved
        # verbatim to window_settings_advcards.py. Assertions unchanged.
        tree = _tree("window_settings_advcards.py")
        found_call = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) \
                    and isinstance(node.func, ast.Name) \
                    and node.func.id == "_about_hint_text":
                found_call = True
                self.assertEqual(len(node.args), 2)
                engine_arg = node.args[1]
                self.assertIsInstance(engine_arg, ast.Call)
                self.assertEqual(engine_arg.func.attr, "_brain_engine")
        self.assertTrue(found_call, "no _about_hint_text call site found")
        # The count feeding it comes from the registry, same source
        # the Skills page uses.
        self.assertIn("get_declarations",
                      _source("window_settings_advcards.py"))

    def test_hint_text_logic(self):
        hint = _load_about_hint()
        self.assertEqual(
            hint(641, "gemini"),
            "Thinks with Google's cloud brain, remembers, sees, "
            "and has 641 skills.")
        self.assertEqual(
            hint(641, "local"),
            "Thinks with the brain on this PC, remembers, sees, "
            "and has 641 skills.")
        self.assertEqual(
            hint(None, "gemini"),
            "Thinks with Google's cloud brain, remembers, and sees.")
        self.assertEqual(
            hint(None, "local"),
            "Thinks with the brain on this PC, remembers, and sees.")
        # Unknown engine falls back to the cloud phrase; 0 is a real
        # count (None means unknown), not "no count".
        self.assertIn("Google's cloud brain", hint(641, "bogus"))
        self.assertIn("has 0 skills", hint(0, "gemini"))


class TestMotionGate(unittest.TestCase):
    def test_gate_definition_untouched_in_substance(self):
        # INTENTIONAL pin: the batch must consult the gate, never
        # redefine it. The fail-open-on-error return is the gate's
        # own contract and is preserved verbatim.
        # INTENTIONAL (refactor P1 step 5): the motion gate moved
        # verbatim to window_settings_appearance.py. Assertions unchanged.
        src = _source("window_settings_appearance.py")
        self.assertIn('return self._motion_level() != "off"', src)
        self.assertIn("Chrome gate (unchanged callers)", src)

    def test_startup_fade_consults_gate_before_fading(self):
        # GUI split #9 INTENTIONAL: showEvent lives in the nav module now.
        func = _func(_tree("window_chat_nav.py"), "showEvent")
        body = list(ast.iter_child_nodes(func))
        # Order matters: the gate check must come before the first
        # setWindowOpacity(0.0) (the fade's opening frame).
        gate_pos = fade_pos = None
        for i, stmt in enumerate(body):
            dump = ast.dump(stmt)
            if gate_pos is None and "_animate_enabled" in dump:
                gate_pos = i
            if fade_pos is None and "setWindowOpacity" in dump \
                    and "0.0" in dump:
                fade_pos = i
        self.assertIsNotNone(gate_pos, "no gate check in showEvent")
        self.assertIsNotNone(fade_pos, "no fade start in showEvent")
        self.assertLess(gate_pos, fade_pos,
                        "gate check must precede the fade")
        # The off-branch sets the final state immediately.
        self.assertTrue(_opacity_calls(func, 1.0),
                        "no setWindowOpacity(1.0) in showEvent")
        # The animation itself is still there (gated, not deleted).
        self.assertIn("DURATION_WINDOW_FADE_MS", ast.dump(func))

    def test_palette_fade_consults_parent_gate(self):
        # INTENTIONAL (refactor P1 step 5): CommandPalette moved verbatim
        # to window_widgets_palette.py. Assertions unchanged.
        func = _func(_tree("window_widgets_palette.py"), "open_centered")
        dump = ast.dump(func)
        self.assertIn("_animate_enabled", dump)
        self.assertIn("_palette_animate", dump)
        self.assertTrue(_opacity_calls(func, 1.0),
                        "no setWindowOpacity(1.0) in open_centered")
        # The animation itself is still there (gated, not deleted).
        self.assertIn("DURATION_PALETTE_FADE_MS", dump)


class TestRemindersScroll(unittest.TestCase):
    def test_page_has_widget_resizable_scroll_area(self):
        # GUI split #8 INTENTIONAL: _build_reminders_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        func = _func(_tree("window_pages_activity.py"), "_build_reminders_page")
        dump = ast.dump(func)
        self.assertIn("QScrollArea", dump)
        resizable = [c for c in _attr_calls(func, "setWidgetResizable")
                     if len(c.args) == 1
                     and isinstance(c.args[0], ast.Constant)
                     and c.args[0].value is True]
        self.assertTrue(resizable, "no setWidgetResizable(True) call")
        self.assertTrue(_attr_calls(func, "setWidget"),
                        "scroll area never given a content widget")

    def test_existing_widget_names_preserved(self):
        # GUI split #8 INTENTIONAL: _build_reminders_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        func = _func(_tree("window_pages_activity.py"), "_build_reminders_page")
        assigned = set()
        for node in ast.walk(func):
            if isinstance(node, ast.Attribute) \
                    and isinstance(node.value, ast.Name) \
                    and node.value.id == "self":
                assigned.add(node.attr)
        # UI rethink Build 2: the old summary label is gone —
        # its line IS the Scheduled headline now, so the
        # attribute no longer exists to be assigned.
        for name in ("reminders_list",
                     "input_reminder_msg", "input_reminder_min",
                     "chk_reminder_at", "input_reminder_h",
                     "input_reminder_m", "spin_brief_hour",
                     "routines_container", "plans_container"):
            self.assertIn(name, assigned, f"{name} lost in restructure")

    def test_stale_session_only_subtitle_corrected(self):
        # INTENTIONAL (disclosed scope-adjacent one-liner): the old
        # subtitle contradicted the shipped scheduler persistence.
        # GUI split #8 INTENTIONAL: _build_reminders_page moved verbatim to
        # window_pages_activity.py. Assertion unchanged.
        func = _func(_tree("window_pages_activity.py"), "_build_reminders_page")
        dump = ast.dump(func)
        self.assertNotIn("live only in this session", dump)
        self.assertIn("survive a restart", dump)

    def test_scheduler_wiring_untouched(self):
        # GUI split #12 batch 1 INTENTIONAL: scheduler strings live in
        # _add_reminder_from_ui/_refresh_reminders, moved verbatim to
        # window_memory_learned.py. Assertion unchanged.
        src = (TREE / "gui" / "window_memory_learned.py").read_text(encoding="utf-8")
        self.assertIn("scheduler.add_reminder", src)
        # T-8 (debug pass): GAPS-28 G-V1 moved the scheduler READ to
        # reminders_view() in gui/memory_view.py — the mixin calls
        # reminders_view(), and the get_all_reminders call lives
        # inside reminders_view, not in the mixin.
        self.assertIn("reminders_view", src)
        view_src = (TREE / "gui" / "memory_view.py").read_text(encoding="utf-8")
        self.assertIn("scheduler.get_all_reminders", view_src)


class TestOnboarding(unittest.TestCase):
    def test_no_unconditional_fixed_size(self):
        src = _source("onboarding.py")
        self.assertNotIn("setFixedSize(780, 560)", src)
        self.assertIn("setMinimumSize(420, 300)", src)

    def test_initial_size_constrained_to_work_area(self):
        src = _source("onboarding.py")
        self.assertIn("primaryScreen", src)
        self.assertIn("availableGeometry", src)
        self.assertIn("min(780", src)
        self.assertIn("min(560", src)

    def test_pages_scroll_inside_dialog(self):
        func = _method(_tree("onboarding.py"), "OnboardingDialog",
                       "__init__")
        dump = ast.dump(func)
        self.assertIn("QScrollArea", dump)
        widgets = [c for c in _attr_calls(func, "setWidget")]
        self.assertTrue(
            any(isinstance(c.args[0], ast.Attribute)
                and c.args[0].attr == "pages" for c in widgets),
            "scroll area never given self.pages")
        self.assertTrue(_attr_calls(func, "setWidgetResizable"),
                        "no setWidgetResizable call in __init__")

    def test_choice_cards_and_flow_untouched_in_shape(self):
        # INTENTIONAL (GUI split #13 batch 1): the dialog is now
        # composed of shell (onboarding.py) + pages mixin
        # (onboarding_pages.py) + widgets (onboarding_widgets.py);
        # _page_choice lives in the pages mixin and _ChoiceCard in
        # widgets, both verbatim. The composed source keeps every
        # assertion unchanged.
        src = (_source("onboarding.py")
               + _source("onboarding_pages.py")
               + _source("onboarding_widgets.py"))
        tree = ast.parse(src)
        _func(tree, "_page_choice")  # still defined
        self.assertIn("class _ChoiceCard", src)
        self.assertIn("self.choice_cards", src)
        self.assertIn("self.btn_next", src)
        self.assertIn("self.btn_back", src)


class TestScopePins(unittest.TestCase):
    def test_version_untouched(self):
        version_src = (TREE / "core" / "version.py").read_text(
            encoding="utf-8")
        # INTENTIONAL gate repin (v6.3.0 gate): the gate bump itself moves
        # VERSION 6.2.0 -> 6.3.0; the pin follows the bump, nothing else.
        self.assertIn('VERSION = "6.3.0"', version_src)

    def test_no_stale_count_in_edited_gui_files(self):
        # GUI split #8 INTENTIONAL: window_pages.py split verbatim into activity +
        # memory + system; the stale-count check follows the moved
        # content. Assertion unchanged.
        # GUI split #9 INTENTIONAL: window_chat.py split verbatim into nav +
        # bubbles + history; the stale-count check follows the moved
        # content. Assertion unchanged.
        for name in ("window_settings.py", "window_chat.py",
                     "window_chat_nav.py", "window_chat_bubbles.py",
                     "window_chat_history.py",
                     "window_widgets.py", "window_pages.py",
                     "window_pages_activity.py", "window_pages_memory.py",
                     "window_pages_system.py",
                     "onboarding.py"):
            self.assertNotIn("494", _source(name), name)


if __name__ == "__main__":
    unittest.main()
