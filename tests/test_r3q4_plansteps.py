"""Round-3 fix Q4 (2026-11-20): create_plan description/schema mismatch.

Section B, parent-verified (Slice-3 F5): create_plan's description
says each step is "either a plain text string, or an object with
'text' plus optional 'tool' and 'args'", and the steps description
says "strings, or objects" — but the schema said steps.items is
{"type": "object"}, so a model that followed the description got its
call refused at the gate ("'steps[0]' must be an object (got
str)"), while _parse_steps would have accepted the very same steps.
Description, schema, and parser disagreed.

The fix moves the SCHEMA, not the description or the parser: items
accept either form (anyOf string/object — the established pattern,
cf. base_convert/epoch_convert/roman_convert). The description's
promise is what voice needs — a spoken plan's steps arrive as plain
text — and the parser already handles them. Validation is not
weakened: a string item must be a string, an object item keeps its
declared properties, and anything else (number, bool, null, array)
still fails; the parser's own checks (empty step, object without
text, step cap) still apply. The alternative — tightening the
description to objects-only and making the parser reject strings —
would have been a behaviour regression for the voice-friendly form
the description promises.

This suite pins the fix, so the three can never drift apart again:
description, schema, parser, and the model-facing Gemini copy must
all agree that a step is a string or an object.

Stdlib unittest only. Plans go to a temp file (_PLANS_PATH patched
on all three planner modules). The funnel executes write
receipts/records as every funnel-driving suite does; the leg
harness restores data/ afterwards.
"""

import json
import pathlib
import shutil
import tempfile
import unittest

import core.brain.planner as _planner_facade  # noqa: E402
import core.brain.planner_checkpoint as _planner_checkpoint  # noqa: E402
import core.brain.planner_plans as _planner_plans  # noqa: E402
from core import confirm_gate  # noqa: E402
from core.brain.planner_storage import MAX_PLAN_STEPS  # noqa: E402
from core.funnel.tool_classification import _CONFIRM_GATE_TOOLS  # noqa: E402
from core.funnel.toolbox_declarations import gemini_decl_for_names  # noqa: E402
from core.funnel.tools_registry import tools  # noqa: E402
from core.funnel.tools_registry_validators import _validate_schema  # noqa: E402


def _body(out):
    if isinstance(out, dict):
        return str(out.get("result", "") or out.get("message", ""))
    return str(out)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        self._orig = (_planner_facade._PLANS_PATH,
                      _planner_plans._PLANS_PATH,
                      _planner_checkpoint._PLANS_PATH)
        for mod in (_planner_facade, _planner_plans,
                    _planner_checkpoint):
            mod._PLANS_PATH = self._tmp / "plans.json"
        confirm_gate.clear_handler()

    def tearDown(self):
        confirm_gate.clear_handler()
        (_planner_facade._PLANS_PATH, _planner_plans._PLANS_PATH,
         _planner_checkpoint._PLANS_PATH) = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def call(self, name, args):
        """One funnel call with a FRESH args dict (never reused:
        the funnel annotates the caller's dict)."""
        return tools.execute(name, dict(args))

    def assert_success(self, out):
        self.assertIsInstance(out, dict)
        self.assertEqual(out.get("status"), "success", str(out))

    def assert_refused(self, out):
        self.assertIsInstance(out, dict)
        self.assertIn(out.get("status"), ("refused", "failure"),
                      str(out))


class DescriptionSchemaParserAgreementTests(_Base):
    """The three agree: a step is a plain string or an object."""

    def test_description_promises_string_steps(self):
        decl = [d for d in tools.get_declarations()
                if d["name"] == "create_plan"][0]
        self.assertIn("plain text string", decl["description"])
        steps_desc = decl["parameters"]["properties"]["steps"][
            "description"]
        self.assertIn("strings", steps_desc)
        self.assertIn("objects", steps_desc)

    def test_schema_items_accept_string_or_object(self):
        decl = [d for d in tools.get_declarations()
                if d["name"] == "create_plan"][0]
        items = decl["parameters"]["properties"]["steps"]["items"]
        self.assertEqual(
            [b.get("type") for b in items.get("anyOf", [])],
            ["string", "object"],
            "round-3 fix Q4 (2026-11-20): items must accept both "
            "forms the description promises")
        obj = items["anyOf"][1]
        self.assertEqual(set(obj["properties"]),
                         {"text", "tool", "args"})
        self.assertEqual(obj.get("required"), [])

    def test_schema_passes_registry_lint(self):
        decl = [d for d in tools.get_declarations()
                if d["name"] == "create_plan"][0]
        self.assertIsNone(
            _validate_schema("create_plan", "",
                             decl["parameters"]),
            "the anyOf items node must be a well-formed schema")

    def test_gemini_declaration_matches_local_schema(self):
        decls = gemini_decl_for_names(tools, ["create_plan"])
        self.assertEqual(len(decls), 1)
        items = decls[0]["parameters"]["properties"]["steps"]["items"]
        self.assertEqual(
            [b.get("type") for b in items.get("anyOf", [])],
            ["string", "object"],
            "the model-facing copy must carry the same schema the "
            "gate enforces, or the model is promised a form the "
            "gate refuses")

    def test_parser_accepts_string_steps(self):
        parsed, err = _planner_plans._parse_steps(
            ["pack a bag", "charge the phone"])
        self.assertIsNone(err)
        self.assertEqual([s["text"] for s in parsed],
                         ["pack a bag", "charge the phone"])
        self.assertTrue(all(s["tool"] is None for s in parsed))

    def test_parser_accepts_json_string_of_steps(self):
        parsed, err = _planner_plans._parse_steps(
            json.dumps(["pack a bag", {"text": "charge the phone"}]))
        self.assertIsNone(err)
        self.assertEqual(len(parsed), 2)

    def test_parser_accepts_bare_string_as_one_step(self):
        parsed, err = _planner_plans._parse_steps("pack a bag")
        self.assertIsNone(err)
        self.assertEqual([s["text"] for s in parsed], ["pack a bag"])


class EndToEndTests(_Base):
    """The promised forms work through the real funnel path."""

    def test_string_steps_create_plan(self):
        out = self.call("create_plan", {
            "goal": "get ready for my trip",
            "steps": ["pack a bag", "charge the phone"]})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("created: get ready for my trip", body)
        self.assertIn("1. pack a bag", body)
        self.assertIn("2. charge the phone", body)
        self.assertNotIn("[via", body)
        out = self.call("get_plan", {"plan_id": "plan-1"})
        self.assert_success(out)
        self.assertIn("1. pack a bag (pending)", _body(out))
        self.assertIn("2. charge the phone (pending)", _body(out))

    def test_mixed_steps_create_plan(self):
        out = self.call("create_plan", {
            "goal": "mixed steps",
            "steps": ["pack a bag",
                      {"text": "Set the volume to 40%",
                       "tool": "set_volume_level",
                       "args": {"level": 40}}]})
        self.assert_success(out)
        body = _body(out)
        self.assertIn("1. pack a bag", body)
        self.assertIn("2. Set the volume to 40% "
                      "[via set_volume_level]", body)

    def test_object_steps_still_work(self):
        out = self.call("create_plan", {
            "goal": "object steps",
            "steps": [{"text": "pack a bag"}]})
        self.assert_success(out)
        self.assertIn("1. pack a bag", _body(out))


class ValidationNotWeakenedTests(_Base):
    """The bad forms still refuse, with a clear message."""

    def test_number_step_refused(self):
        self.assertIsNotNone(
            tools.validate_args("create_plan", {
                "goal": "g", "steps": [42]}))
        out = self.call("create_plan", {"goal": "g", "steps": [42]})
        self.assert_refused(out)
        self.assertIn("anyOf", _body(out))

    def test_bool_null_and_list_steps_refused(self):
        for bad in (True, None, ["pack a bag"]):
            self.assertIsNotNone(
                tools.validate_args("create_plan", {
                    "goal": "g", "steps": [bad]}),
                f"step {bad!r} should fail validation")

    def test_object_step_with_unknown_keys_refused_by_parser(self):
        # The object branch is open (no additionalProperties), as
        # before the fix — so {"n": 1} passes the gate, and the
        # parser refuses it for having no 'text'. Pre-existing
        # shape, unchanged by Q4; pinned so a future tightening
        # of either side shows up here.
        self.assertIsNone(
            tools.validate_args("create_plan", {
                "goal": "g", "steps": [{"n": 1}]}))
        out = self.call("create_plan", {
            "goal": "g", "steps": [{"n": 1}]})
        self.assert_refused(out)
        self.assertIn("no 'text'", _body(out))

    def test_empty_string_step_refused_by_parser(self):
        out = self.call("create_plan", {"goal": "g", "steps": [""]})
        self.assert_refused(out)
        self.assertIn("Step 1 is empty.", _body(out))

    def test_empty_steps_refused(self):
        out = self.call("create_plan", {"goal": "g", "steps": []})
        self.assert_refused(out)
        self.assertIn("non-empty", _body(out))

    def test_missing_goal_refused(self):
        self.assertIsNotNone(
            tools.validate_args("create_plan",
                                {"steps": ["pack a bag"]}))
        out = self.call("create_plan", {"steps": ["pack a bag"]})
        self.assert_refused(out)

    def test_object_step_without_text_refused(self):
        out = self.call("create_plan", {
            "goal": "g", "steps": [{"tool": "quick_math"}]})
        self.assert_refused(out)
        self.assertIn("no 'text'", _body(out))

    def test_step_cap_still_enforced(self):
        out = self.call("create_plan", {
            "goal": "g",
            "steps": [f"step {i}" for i in range(MAX_PLAN_STEPS + 1)]})
        self.assert_refused(out)
        self.assertIn(f"capped at {MAX_PLAN_STEPS}", _body(out))


class GatesUnmovedTests(_Base):
    """Creating a plan runs nothing, so it needs no confirmation;
    running one still does (fix #4, T12)."""

    def test_create_plan_not_confirm_gated(self):
        self.assertNotIn("create_plan", _CONFIRM_GATE_TOOLS)

    def test_run_plan_still_confirm_gated(self):
        self.assertIn("run_plan", _CONFIRM_GATE_TOOLS)


if __name__ == "__main__":
    unittest.main()
