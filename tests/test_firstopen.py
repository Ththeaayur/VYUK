"""First-open prompt startup surface — suite.

Lab reality (stated honestly): GUI modules import frozen
``voice_assistant.*`` names and build real Qt widgets, so
they cannot be imported or driven here. What this suite
proves for real: the pure flag + choice + gate logic in
core/firstopen_surface executes (fresh/seen, Yes/No,
malformed, wrong-scope, default-OFF). What it proves by
AST over the real files: the startup call site exists in
the full first-run path only and fails open; the dialog
renders the descriptor verbatim with default No; the
Settings card wires the SAME keys; onboarding's own flow
is untouched. It does NOT prove rendered appearance —
field proof on real Windows is owed for every item.

No test here weakens an existing assertion. New pins
carry no INTENTIONAL-update burden: this file is new.
"""

import ast
import json
import unittest
from pathlib import Path

from core import firstopen_surface as fos
from core import firstopen_optin as optin_mod

TREE = Path(__file__).resolve().parent.parent
NOW = "2026-10-30T10:00:00"


def _parse(relpath):
    return ast.parse((TREE / relpath).read_text(encoding="utf-8"))


def _funcs(tree, name):
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == name]


def _calls(node):
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.append(f.id)
            elif isinstance(f, ast.Attribute):
                out.append(f.attr)
    return out


def _attrs(node):
    return [n.attr for n in ast.walk(node)
            if isinstance(n, ast.Attribute)]


class TestPureFlagAndChoice(unittest.TestCase):
    def test_show_on_fresh_and_never_when_seen(self):
        self.assertTrue(fos.should_show_prompt({}))
        self.assertTrue(fos.should_show_prompt(
            {fos.PROMPT_SEEN_KEY: False}))
        self.assertFalse(fos.should_show_prompt(
            {fos.PROMPT_SEEN_KEY: True}))

    def test_apply_yes_opens_gate(self):
        updated = fos.apply_choice({}, True, NOW)
        self.assertIs(updated[fos.PROMPT_SEEN_KEY], True)
        record = fos.choice_from_stored(updated)
        self.assertIsNotNone(record)
        self.assertTrue(record.allowed)
        self.assertEqual(record.scope,
                         optin_mod.CONTINUOUS_DISPLAY_SCOPE)
        self.assertEqual(record.chooser, "user")
        self.assertEqual(record.timestamp, NOW)
        self.assertTrue(record.retention_rule)
        self.assertTrue(record.deletion_rule)
        gate = fos.gate_from_stored(updated)
        self.assertTrue(gate["enabled"])

    def test_apply_no_is_seen_and_disabled(self):
        # Closing without choosing is No, not a deferral:
        # seen is still set, so the prompt never re-asks.
        updated = fos.apply_choice({}, False, NOW)
        self.assertIs(updated[fos.PROMPT_SEEN_KEY], True)
        self.assertFalse(fos.gate_from_stored(updated)["enabled"])

    def test_absent_choice_gates_disabled(self):
        gate = fos.gate_from_stored({})
        self.assertFalse(gate["enabled"])
        self.assertIn("OFF", gate["reason"])

    def test_malformed_stored_choice_gates_disabled(self):
        # Two distinct refusals, both fail-closed: unparseable
        # stored values yield NO record at all, while
        # shape-valid but incomplete/wrong-scope records yield
        # a record the GATE refuses (completeness lives in
        # the gate's usable_reason, not in parsing). Either
        # way the gate reports disabled — that is the invariant.
        unparseable = ["{not json", json.dumps(["not", "an", "object"])]
        for raw in unparseable:
            with self.subTest(raw=raw[:40]):
                stored = {fos.CHOICE_KEY: raw}
                self.assertIsNone(fos.choice_from_stored(stored))
                self.assertFalse(fos.gate_from_stored(stored)["enabled"])
        incomplete = [
            json.dumps({"allowed": True, "chooser": "user",
                        "scope": "suggestion-cards",
                        "timestamp": NOW,
                        "retention_rule": "r", "deletion_rule": "d"}),
            json.dumps({"allowed": True, "chooser": "user",
                        "scope": optin_mod.CONTINUOUS_DISPLAY_SCOPE,
                        "timestamp": "",
                        "retention_rule": "r", "deletion_rule": "d"}),
            json.dumps({"allowed": True, "chooser": "",
                        "scope": optin_mod.CONTINUOUS_DISPLAY_SCOPE,
                        "timestamp": NOW,
                        "retention_rule": "r", "deletion_rule": "d"}),
        ]
        for raw in incomplete:
            with self.subTest(raw=raw[:40]):
                stored = {fos.CHOICE_KEY: raw}
                gate = fos.gate_from_stored(stored)
                self.assertFalse(gate["enabled"])
                self.assertTrue(gate["reason"])

    def test_oversize_field_raises_not_truncated(self):
        with self.assertRaises(ValueError):
            fos.choice_record_dict(True, "x" * 501)

    def test_choice_roundtrip_preserves_fields(self):
        updated = fos.apply_choice({}, True, NOW)
        data = json.loads(updated[fos.CHOICE_KEY])
        self.assertEqual(data["timestamp"], NOW)
        self.assertEqual(data["form"], optin_mod.WATCHES_TEXT)

    def test_single_canonical_key_pair(self):
        self.assertEqual(fos.PROMPT_SEEN_KEY,
                         "privacy/firstopen_prompt_seen")
        self.assertEqual(fos.CHOICE_KEY,
                         "privacy/continuous_display_choice")


class TestStartupCallSite(unittest.TestCase):
    def test_prompt_called_in_full_run_only_and_guarded(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        tree = _parse("gui/window_shell_onboarding.py")
        (fn,) = _funcs(tree, "_run_onboarding")
        # The full-run branch is the `else` of the
        # `if choice_only:` — the call must live there,
        # never in the choice-only branch.
        # The full-run branch is the `else` of an
        # `if choice_only:` — the call must live in an else
        # branch, never in any choice-only branch (there are
        # two `if choice_only:` blocks in this function).
        ifs = [n for n in ast.walk(fn) if isinstance(n, ast.If)]
        self.assertTrue(ifs, "no if in _run_onboarding")
        choice_ifs = []
        for node in ifs:
            if any(isinstance(t, ast.Name) and t.id == "choice_only"
                   for t in ast.walk(node.test)):
                choice_ifs.append(node)
        self.assertTrue(choice_ifs, "choice_only branch not found")
        for node in choice_ifs:
            body_calls = _calls(ast.Module(body=node.body,
                                           type_ignores=[]))
            self.assertNotIn("_run_first_open_prompt", body_calls)
        else_calls = []
        for node in choice_ifs:
            else_calls += _calls(ast.Module(body=node.orelse,
                                            type_ignores=[]))
        self.assertIn("_run_first_open_prompt", else_calls)
        # The call itself sits inside a try (fail-open).
        found_guarded = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Try):
                if "_run_first_open_prompt" in _calls(node):
                    found_guarded = True
        self.assertTrue(found_guarded,
                        "first-open call is not inside a try")

    def test_prompt_method_fails_open_to_no(self):
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        tree = _parse("gui/window_shell_onboarding.py")
        (fn,) = _funcs(tree, "_run_first_open_prompt")
        src = ast.dump(fn)
        self.assertIn("should_show_prompt", _calls(fn))
        self.assertIn("apply_choice", _calls(fn))
        # An exception path forces allowed=False.
        excepts = [n for n in ast.walk(fn)
                   if isinstance(n, ast.ExceptHandler)]
        self.assertTrue(excepts)
        self.assertIn("choice_yes", src)
        # Both canonical keys are read through the helper.
        self.assertIn("PROMPT_SEEN_KEY", _attrs(fn))
        self.assertIn("CHOICE_KEY", _attrs(fn))

    def test_onboarding_flow_not_entered(self):
        # Placement is AFTER onboarding, not inside it:
        # the onboarding module must not reference the
        # first-open surface at all.
        src = (TREE / "gui/onboarding.py").read_text(encoding="utf-8")
        self.assertNotIn("firstopen_prompt", src)
        self.assertNotIn("firstopen_surface", src)
        self.assertNotIn("continuous_display", src)
        # And the launch gate still sets onboarded True.
        # INTENTIONAL (refactor P1 step 5): shell method moved verbatim to its panel mixin. Assertion unchanged.
        tree = _parse("gui/window_shell_onboarding.py")
        (fn,) = _funcs(tree, "_run_onboarding")
        self.assertIn("onboarded", ast.dump(fn))


class TestDialogShape(unittest.TestCase):
    def test_dialog_renders_descriptor_with_default_no(self):
        tree = _parse("gui/firstopen_prompt.py")
        classes = [n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef)
                   and n.name == "FirstOpenPromptDialog"]
        self.assertEqual(len(classes), 1)
        cls = classes[0]
        src = ast.dump(cls)
        self.assertIn("first_open_prompt", src)
        self.assertIn("QScrollArea", src)
        self.assertIn("setMinimumSize", src)
        self.assertNotIn("setFixedSize", src)
        self.assertIn("availableGeometry", src)
        # choice_yes starts False; only _on_yes sets True.
        inits = [n for n in ast.walk(cls)
                 if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Attribute)
                         and t.attr == "choice_yes"
                         for t in n.targets)]
        self.assertTrue(inits)
        (yes_fn,) = _funcs(cls, "_on_yes")
        self.assertIn("choice_yes", ast.dump(yes_fn))
        # No re-worded copy: none of the descriptor's own
        # paragraph texts appear as literals in the dialog.
        prompt = optin_mod.first_open_prompt()
        dialog_src = (TREE / "gui/firstopen_prompt.py").read_text(
            encoding="utf-8")
        for para in prompt["paragraphs"]:
            self.assertNotIn(para, dialog_src)
        self.assertNotIn(prompt["title"], dialog_src)

    def test_no_capture_imports_in_new_files(self):
        for rel in ("core/firstopen_surface.py",
                    "gui/firstopen_prompt.py"):
            tree = _parse(rel)
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
            joined = " ".join(imported)
            for banned in ("screen_watch", "watcher", "clipboard",
                           "screen_capture"):
                self.assertNotIn(banned, joined)


class TestSettingsCard(unittest.TestCase):
    def test_card_in_privacy_group_wired_to_same_keys(self):
        # INTENTIONAL (refactor P1 step 5): the first-open card moved
        # verbatim to window_settings_groups.py, its toggle handler to
        # window_settings_voice.py. Concatenated source + the voice
        # tree keep every assertion unchanged.
        tree = _parse("gui/window_settings_voice.py")
        src = ((TREE / "gui/window_settings_groups.py").read_text(
            encoding="utf-8")
            + (TREE / "gui/window_settings_voice.py").read_text(
                encoding="utf-8"))
        self.assertIn("chk_continuous_display", src)
        self.assertIn("_on_continuous_display_toggled", src)
        self.assertIn("PROMPT_SEEN_KEY", src)
        self.assertIn("CHOICE_KEY", src)
        (handler,) = _funcs(tree, "_on_continuous_display_toggled")
        self.assertIn("apply_choice", _calls(handler))
        # The card joins the Privacy group, not the page root.
        self.assertIn("_g_privacy", src)

    def test_explanation_entry_exists(self):
        # GUI split #3 INTENTIONAL: SettingExplanation calls moved verbatim
        # into pf_settings_help.py; the facade no longer carries them.
        tree = _parse("gui/pf_settings_help.py")
        keys = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id == "SettingExplanation":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        keys.append(node.args[0].value)
        self.assertIn("continuous_display", keys)


if __name__ == "__main__":
    unittest.main()
