"""RET-5 (2026-11-15): routine-template gallery + build-in-public demos.

A template is data, never code: a named list of step descriptions
(tool + arg-shape with placeholders), validated on export AND on
import. v1 templates are READ-class only; export is confirm-gated
and shows the exact JSON first; import goes through the funnel's
confirm-gated save routine action and never pre-approves the
steps. No new tool, census unchanged. The demo checklist is a
standing document, not code.

The validator edges are pinned here with a fake registry (the
RET-2 pattern); the round-trip and bundled-template tests run
the REAL registry on both interpreters. The GUI panel tests
import the mixin-free module when PySide6 is present and verify
statically otherwise (the test_gaps23.py Qt-guard pattern).
"""

import json
import sys
import types
import unittest
from pathlib import Path

import core.routine_templates as rt
from core.funnel.tools_gate_readclass import _is_read_class

TREE = Path(__file__).resolve().parent.parent
# The gui modules import voice_assistant.core.* (the shipped
# spelling), so the alias is planted first (the test_gaps27.py /
# test_ret3.py pattern).
_pkg = types.ModuleType("voice_assistant")
_pkg.__path__ = [str(TREE)]
sys.modules.setdefault("voice_assistant", _pkg)
SECRET_HEX = ("a3f9c1e7b2d84f6a0c5e9d3b7a1f4c8e2d6b0a9f3c7e5d1b8"
              "a6c4e0f2d8a6b4c0e9f1a3d5b7c9e1f3a5d7b9c1e3f5a7b9d1e3f5")
LONG_BLOB = "x" * 5000


class _FakeRegistry:
    """Minimal registry protocol for the validator: validate_args
    returns None when the tool exists and its args validate, else
    an error string; get_declarations carries the arg types so a
    placeholder is checked against the slot's declared type."""

    def __init__(self, tools):
        self.tools = tools  # {name: {arg: type, "required": (...)}}

    def get_declarations(self):
        return [{"name": n,
                 "parameters": {"properties": {
                     k: {"type": v} for k, v in spec.items()
                     if k != "required"},
                     "additionalProperties": False}}
                for n, spec in self.tools.items()]

    def validate_args(self, name, args):
        if name not in self.tools:
            return "Unknown tool '%s'." % name
        if not isinstance(args, dict):
            return "args must be an object"
        for req in self.tools[name].get("required", ()):
            if req not in args:
                return "'%s' is required." % req
        for key, value in args.items():
            want = self.tools[name].get(key)
            if want == "string" and not isinstance(value, str):
                return "'%s' must be a string (got %s)." % (
                    key, type(value).__name__)
            if want == "integer" and not isinstance(value, int):
                return "'%s' must be an integer (got %s)." % (
                    key, type(value).__name__)
        return None


_FAKE = _FakeRegistry({
    "fake_read": {"text": "string"},
    "fake_read_named": {"contact": "string", "required": ("contact",)},
    "fake_read_num": {"count": "integer"},
    "fake_gated": {},
})
_FAKE_READ = lambda name, args: name in (
    "fake_read", "fake_read_named", "fake_read_num")


def _build(steps, **kw):
    return rt.build_template({"name": "probe", "actions": steps},
                             registry=_FAKE, is_read=_FAKE_READ, **kw)


class TestFormatAndApi(unittest.TestCase):
    def test_format_version_is_1(self):
        self.assertEqual(rt.TEMPLATE_FORMAT_VERSION, 1)
        self.assertEqual(rt.MAX_TEMPLATE_BYTES, 65536)

    def test_validator_api_present(self):
        for name in ("build_template", "validate_template_text",
                     "import_template", "export_template",
                     "template_json", "load_bundled_templates",
                     "extract_placeholders"):
            self.assertTrue(callable(getattr(rt, name)), name)

    def test_template_json_is_canonical(self):
        template = {"format_version": 1, "name": "t",
                    "steps": [{"tool": "fake_read", "args": {}}]}
        text = rt.template_json(template)
        self.assertEqual(json.loads(text), template)
        self.assertEqual(text, json.dumps(template, indent=2,
                                          sort_keys=True))


class TestExportRefusals(unittest.TestCase):
    def test_export_refuses_absolute_path(self):
        for path in ("/home/aayur/notes.txt",
                     "C:\\Users\\aayur\\notes.txt",
                     "\\\\server\\share\\notes.txt"):
            ok, _t, reason, _ro = _build(
                [{"tool": "fake_read", "args": {"path": path}}])
            self.assertFalse(ok, path)
            self.assertIn("path", reason.lower(), reason)

    def test_export_refuses_name_in_person_slot(self):
        ok, _t, reason, _ro = _build(
            [{"tool": "fake_read_named",
              "args": {"contact": "Aayur Gattani"}}])
        self.assertFalse(ok)
        self.assertIn("name", reason.lower(), reason)

    def test_export_allows_name_like_value_in_other_slot(self):
        # The name rule is key-aware: "New York" under a location
        # key is a place, not a person. Pinned so a later batch
        # does not "fix" it into a blanket capital-words refusal.
        ok, template, reason, _ro = _build(
            [{"tool": "fake_read", "args": {"text": "New York"}}])
        self.assertTrue(ok, reason)
        self.assertEqual(template["name"], "probe")

    def test_export_refuses_email(self):
        ok, _t, reason, _ro = _build(
            [{"tool": "fake_read",
              "args": {"text": "mail me at aayur@example.com please"}}])
        self.assertFalse(ok)
        self.assertIn("email", reason.lower(), reason)

    def test_export_refuses_secret(self):
        ok, _t, reason, _ro = _build(
            [{"tool": "fake_read", "args": {"token": SECRET_HEX}}])
        self.assertFalse(ok)
        self.assertIn("secret", reason.lower(), reason)

    def test_export_refuses_file_content(self):
        for blob in (LONG_BLOB, "a\x00b",
                     "data:text/plain;base64,SGVsbG8="):
            ok, _t, reason, _ro = _build(
                [{"tool": "fake_read", "args": {"blob": blob}}])
            self.assertFalse(ok, blob[:20])
            self.assertIn("content", reason.lower(), reason)

    def test_export_refuses_code_bearing_value(self):
        ok, _t, reason, _ro = _build(
            [{"tool": "fake_read",
              "args": {"x": "eval(__import__('os'))"}}])
        self.assertFalse(ok)
        self.assertIn("code", reason.lower(), reason)

    def test_export_refuses_unknown_tool(self):
        ok, _t, reason, _ro = _build(
            [{"tool": "ghost_tool", "args": {}}])
        self.assertFalse(ok)
        self.assertIn("ghost_tool", reason)

    def test_export_refuses_bad_args(self):
        ok, _t, reason, _ro = _build(
            [{"tool": "fake_read_named", "args": {}}])
        self.assertFalse(ok)
        self.assertIn("contact", reason)

    def test_export_refuses_gated_step_and_offers_read_only(self):
        ok, _t, reason, read_only = _build(
            [{"tool": "fake_read", "args": {}},
             {"tool": "fake_gated", "args": {}}])
        self.assertFalse(ok)
        self.assertIn("gated", reason.lower(), reason)
        self.assertEqual(len(read_only), 1)
        self.assertEqual(read_only[0]["tool"], "fake_read")
        ok2, template, _r2, _ro2 = _build(
            [{"tool": "fake_read", "args": {}},
             {"tool": "fake_gated", "args": {}}], read_only_only=True)
        self.assertTrue(ok2)
        self.assertEqual(len(template["steps"]), 1)
        self.assertEqual(template["steps"][0]["tool"], "fake_read")

    def test_export_refuses_empty_routine(self):
        ok, _t, reason, _ro = rt.build_template(
            {"name": "empty", "actions": []},
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertFalse(ok)


class TestExportConfirmation(unittest.TestCase):
    def test_export_without_confirmation_refused(self):
        ok, template, _r, _ro = _build(
            [{"tool": "fake_read", "args": {"text": "hello"}}])
        self.assertTrue(ok)
        ok2, _js, reason = rt.export_template(template, confirmed=False)
        self.assertFalse(ok2)
        self.assertIn("confirmation", reason.lower(), reason)

    def test_export_with_confirmation_returns_exact_json(self):
        ok, template, _r, _ro = _build(
            [{"tool": "fake_read", "args": {"text": "hello"}}])
        self.assertTrue(ok)
        ok2, js, reason = rt.export_template(
            template, confirmed=True, registry=_FAKE,
            is_read=_FAKE_READ)
        self.assertTrue(ok2, reason)
        self.assertEqual(json.loads(js), template)


class TestImportRefusals(unittest.TestCase):
    def test_import_refuses_non_json(self):
        ok, _s, reason = rt.validate_template_text("not json {")
        self.assertFalse(ok)
        self.assertIn("json", reason.lower(), reason)

    def test_import_refuses_oversized(self):
        ok, _s, reason = rt.validate_template_text(
            " " * (rt.MAX_TEMPLATE_BYTES + 1))
        self.assertFalse(ok)
        self.assertIn("size", reason.lower(), reason)

    def test_import_refuses_unknown_version(self):
        ok, _s, reason = rt.validate_template_text(json.dumps({
            "format_version": 99, "name": "future",
            "steps": [{"tool": "fake_read", "args": {}}]}),
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertFalse(ok)
        self.assertIn("version", reason.lower(), reason)

    def test_import_refuses_non_read_step(self):
        ok, _s, reason = rt.validate_template_text(json.dumps({
            "format_version": 1, "name": "gated",
            "steps": [{"tool": "fake_gated", "args": {}}]}),
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertFalse(ok)
        self.assertIn("read", reason.lower(), reason)

    def test_import_refuses_unknown_tool(self):
        ok, _s, reason = rt.validate_template_text(json.dumps({
            "format_version": 1, "name": "ghost",
            "steps": [{"tool": "ghost_tool", "args": {}}]}),
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertFalse(ok)
        self.assertIn("ghost_tool", reason)

    def test_import_refuses_missing_placeholder_value(self):
        ok, _s, reason = rt.import_template(json.dumps({
            "format_version": 1, "name": "needs city",
            "steps": [{"tool": "fake_read",
                       "args": {"text": {"$template": "city"}}}]}),
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertFalse(ok)
        self.assertIn("city", reason)

    def test_import_refuses_bad_filled_value(self):
        ok, _s, reason = rt.import_template(json.dumps({
            "format_version": 1, "name": "needs path",
            "steps": [{"tool": "fake_read",
                       "args": {"path": {"$template": "path"}}}]}),
            fill={"path": "/home/aayur/notes.txt"},
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertFalse(ok)
        self.assertIn("path", reason.lower(), reason)

    def test_import_refuses_unknown_placeholder_marker(self):
        ok, _s, reason = rt.validate_template_text(json.dumps({
            "format_version": 1, "name": "weird",
            "steps": [{"tool": "fake_read",
                       "args": {"meta": {"$eval": "x"}}}]}),
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertFalse(ok)
        self.assertIn("marker", reason.lower(), reason)


class TestImportAndPlaceholders(unittest.TestCase):
    def test_placeholders_fill_at_any_depth(self):
        ok, steps, reason = rt.import_template(json.dumps({
            "format_version": 1, "name": "deep",
            "steps": [{"tool": "fake_read",
                       "args": {"text": {"$template": "word"},
                                "meta": {"tags": [{"$template": "tag"}],
                                         "n": {"$template": "n"}}}}]}),
            fill={"word": "hello", "tag": "morning", "n": 3},
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertTrue(ok, reason)
        self.assertEqual(steps[0]["args"]["text"], "hello")
        self.assertEqual(steps[0]["args"]["meta"]["tags"], ["morning"])
        self.assertEqual(steps[0]["args"]["meta"]["n"], 3)

    def test_placeholder_checked_against_schema_type(self):
        # "count" is declared integer: a string dummy would fail
        # schema validation if the placeholder were not replaced
        # with a shape-correct dummy first.
        ok, template, reason = rt.validate_template_text(json.dumps({
            "format_version": 1, "name": "typed",
            "steps": [{"tool": "fake_read_num",
                       "args": {"count": {"$template": "n"}}}]}),
            registry=_FAKE, is_read=_FAKE_READ)
        self.assertTrue(ok, reason)
        self.assertEqual(rt.extract_placeholders(template), ["n"])

    def test_extract_placeholders_first_seen_order(self):
        template = {"format_version": 1, "name": "t",
                    "steps": [{"tool": "fake_read",
                               "args": {"a": {"$template": "city"},
                                        "b": {"$template": "unit"},
                                        "c": {"$template": "city"}}}]}
        self.assertEqual(rt.extract_placeholders(template),
                         ["city", "unit"])

    def test_round_trip_live_registry_all_read(self):
        from core.funnel.tools_registry import tools as live_tools
        ok, template, reason, _ro = rt.build_template(
            {"name": "round trip",
             "actions": [{"tool": "get_current_time_date", "args": {}},
                         {"tool": "battery_info", "args": {}}]},
            registry=live_tools)
        self.assertTrue(ok, reason)
        ok2, js, reason2 = rt.export_template(template, confirmed=True)
        self.assertTrue(ok2, reason2)
        ok3, steps, reason3 = rt.import_template(js, registry=live_tools)
        self.assertTrue(ok3, reason3)
        self.assertEqual(len(steps), 2)
        for step in steps:
            self.assertTrue(
                _is_read_class(step["tool"], step.get("args") or {}),
                step["tool"])


class TestBundledTemplates(unittest.TestCase):
    def test_bundled_file_loads_all_valid(self):
        valid, skipped = rt.load_bundled_templates()
        self.assertEqual(skipped, [])
        self.assertEqual(len(valid), 3)
        names = [t["name"] for t in valid]
        self.assertEqual(names, ["Morning check", "Weather where I am",
                                 "Stock quote"])

    def test_bundled_templates_validate_and_are_read_only(self):
        from core.funnel.tools_registry import tools as live_tools
        valid, _skipped = rt.load_bundled_templates()
        for template in valid:
            ok, _t, reason = rt.validate_template_text(
                json.dumps(template), registry=live_tools)
            self.assertTrue(ok, reason)
            for step in template["steps"]:
                self.assertTrue(
                    _is_read_class(step["tool"], step.get("args") or {}),
                    step["tool"])

    def test_bundled_file_has_no_user_data(self):
        text = (TREE / "routine_templates.json").read_text(
            encoding="utf-8")
        self.assertNotIn("/home/", text)
        self.assertNotIn("C:\\", text)
        self.assertNotIn("/Users/", text)
        self.assertNotIn("sk-", text)


class TestGalleryRenderers(unittest.TestCase):
    def test_render_steps_masks_secrets(self):
        from gui.window_templates import render_template_steps
        text = render_template_steps({
            "steps": [{"tool": "fake_read",
                       "args": {"token": SECRET_HEX}}]})
        self.assertIn("fake_read", text)
        self.assertNotIn(SECRET_HEX, text)
        # summarize_args masks a secret-looking value as bullets.
        self.assertIn("•••", text)

    def test_render_list_names_templates(self):
        from gui.window_templates import render_template_list
        text = render_template_list([
            {"name": "Morning check",
             "steps": [{"tool": "get_current_time_date", "args": {}}]},
            {"name": "Weather where I am",
             "steps": [{"tool": "get_weather", "args": {}}]}])
        self.assertIn("Morning check (1 step", text)
        self.assertIn("Weather where I am (1 step", text)

    def test_gallery_source_is_bundled_only(self):
        from gui.window_templates import GALLERY_SOURCE_TEXT
        self.assertIn("routine_templates.json", GALLERY_SOURCE_TEXT)
        self.assertIn("D-RET-2", GALLERY_SOURCE_TEXT)


class TestDemoChecklist(unittest.TestCase):
    def test_checklist_exists_and_names_the_rules(self):
        import re
        text = (TREE / "DEMO_CHECKLIST.md").read_text(encoding="utf-8")
        flat = re.sub(r"\s+", " ", text.lower())
        for needle in ("per-demo", "lab green", "field proof",
                       "uncut", "never edited into a success",
                       "real machine"):
            self.assertIn(needle, flat, needle)

    def test_no_forbidden_mechanics_in_ret5_files(self):
        # The report's not-to-chase list is a hard constraint:
        # no streak/badge/leaderboard/score, no charging logic.
        # The prohibition itself is named in the module docstrings
        # ("no leaderboard — the not-to-chase list"), so .py files
        # are scanned with the module docstring stripped: the
        # check is about what the code DOES, not what it forbids.
        import ast
        for rel in ("core/routine_templates.py",
                    "gui/window_templates.py",
                    "routine_templates.json", "DEMO_CHECKLIST.md"):
            text = (TREE / rel).read_text(encoding="utf-8")
            if rel.endswith(".py"):
                doc = ast.get_docstring(ast.parse(text)) or ""
                if doc:
                    text = text.replace(doc, "", 1)
            text = text.lower()
            for needle in ("leaderboard", "badge", "streak",
                           "upgrade to", "price: $"):
                self.assertNotIn(needle, text, (rel, needle))

    def test_import_path_uses_the_funnel(self):
        text = (TREE / "gui" / "window_templates.py").read_text(
            encoding="utf-8")
        self.assertIn("tools.execute", text)
        self.assertIn('"save_routine"', text)
        self.assertNotIn("planner.save_routine", text)


class TestCensusUnchanged(unittest.TestCase):
    def test_registry_census_unchanged(self):
        from core.funnel.tools_registry import tools as live_tools
        decls = live_tools.get_declarations()
        params = sum(len(d.get("parameters", {}).get("properties", {}))
                     for d in decls)
        self.assertEqual(len(decls), 756)  # INTENTIONAL — field-check fix #32 (2026-11-18): 755 -> 756 — one new tool run_anticipation_routine
        self.assertEqual(params, 2078)  # INTENTIONAL — RET-2 (2026-11-15): 2073 -> 2076 — scheduled_agents gains 3 properties (name, steps, schedule); Voice-control batch (2026-11-16): 2076 -> 2077 — click_anywhere gains confirm (C4 ask resolver); field-check fix #32 (2026-11-18): 2077 -> 2078 — one new tool run_anticipation_routine


if __name__ == "__main__":
    unittest.main()
