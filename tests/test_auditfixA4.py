"""Audit fix A4 — validator (master audit P1-2) + malformed
dispatch arguments (wiring audit WIR-06).

Validator: the old subset silently accepted below-minimum
numbers, missing nested required fields, extras against a
closed schema, short strings, explicit nulls for non-nullable
fields, bool/enum conflation (True == 1 in Python) and
wrongly-typed array items. Each gap has a positive test (a
valid call still passes) and a negative test (the invalid
call is now rejected). A schema keyword outside the supported
set rejects the call instead of silently accepting it.

Dispatch: core.funnel.toolbox._call and core.brain.domain_routers.
dispatch_route used to turn any provided-but-malformed bundle
into {} and dispatch a no-argument call. Now only an omitted
bundle keeps today's behaviour; a malformed bundle is
rejected with a structured error that keeps the dispatcher
and the inner tool separate and preserves the received input.

Stdlib unittest only. Run via tests/run_tests.py with
PYTHONPATH=. (the suites need it).
"""

import unittest

from core.brain import domain_routers as routers_mod  # Phase 3 B1b INTENTIONAL: domain_routers moved to core/brain/
from core.funnel import toolbox as toolbox_mod  # Phase 3 F2a INTENTIONAL: toolbox moved to core/funnel/
from core.funnel.tools_registry import _validate_args_against_schema as validate  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

_CLOSED = {
    "type": "object",
    "properties": {
        "count": {"type": "integer", "minimum": 5, "maximum": 10},
        "payload": {
            "type": "object",
            "properties": {
                "required_child": {"type": "string"},
            },
            "required": ["required_child"],
            "additionalProperties": False,
        },
        "name": {"type": "string", "minLength": 3, "maxLength": 5},
    },
    "required": ["count"],
    "additionalProperties": False,
}


class _FakeRegistry:
    def __init__(self):
        self.last_args = None

    def get_declarations(self):
        return [{"name": "probe_tool"}, {"name": "feel_orb_map"}]

    def execute(self, name, args):
        self.last_args = args
        return {"status": "success", "result": f"ran {name} {args}"}


class ValidatorBoundsTests(unittest.TestCase):
    def test_minimum_and_maximum(self):
        self.assertIsNone(validate("p", {"count": 5}, _CLOSED))
        self.assertIsNone(validate("p", {"count": 10}, _CLOSED))
        self.assertIsNotNone(validate("p", {"count": 4}, _CLOSED))
        self.assertIsNotNone(validate("p", {"count": 11}, _CLOSED))

    def test_exclusive_bounds(self):
        schema = {"type": "object", "properties": {
            "v": {"type": "number", "exclusiveMinimum": 0,
                  "exclusiveMaximum": 1}}}
        self.assertIsNone(validate("p", {"v": 0.5}, schema))
        self.assertIsNotNone(validate("p", {"v": 0}, schema))
        self.assertIsNotNone(validate("p", {"v": 1}, schema))

    def test_multiple_of(self):
        schema = {"type": "object", "properties": {
            "v": {"type": "number", "multipleOf": 0.5}}}
        self.assertIsNone(validate("p", {"v": 1.5}, schema))
        self.assertIsNotNone(validate("p", {"v": 1.3}, schema))


class ValidatorNestedTests(unittest.TestCase):
    def test_nested_required(self):
        self.assertIsNone(validate(
            "p", {"count": 5, "payload": {"required_child": "a"}},
            _CLOSED))
        problem = validate("p", {"count": 5, "payload": {}}, _CLOSED)
        self.assertIsNotNone(problem)
        self.assertIn("required_child", problem)

    def test_nested_type(self):
        problem = validate(
            "p", {"count": 5, "payload": {"required_child": 3}},
            _CLOSED)
        self.assertIsNotNone(problem)
        self.assertIn("string", problem)


class ValidatorExtrasTests(unittest.TestCase):
    def test_closed_schema_rejects_extras_root_and_nested(self):
        self.assertIsNotNone(
            validate("p", {"count": 5, "extra": 1}, _CLOSED))
        self.assertIsNotNone(validate(
            "p", {"count": 5,
                  "payload": {"required_child": "a", "extra": 2}},
            _CLOSED))

    def test_open_schema_still_allows_extras(self):
        # JSON Schema default: additionalProperties absent means
        # allowed. This is the standing _dispatch_args rationale
        # — several handlers accept **kwargs-style extras.
        open_schema = {"type": "object", "properties": {
            "count": {"type": "integer"}}}
        self.assertIsNone(
            validate("p", {"count": 5, "extra": 1}, open_schema))

    def test_additional_properties_schema_validates_extras(self):
        schema = {"type": "object",
                  "properties": {"count": {"type": "integer"}},
                  "additionalProperties": {"type": "string"}}
        self.assertIsNone(
            validate("p", {"count": 5, "extra": "x"}, schema))
        self.assertIsNotNone(
            validate("p", {"count": 5, "extra": 1}, schema))


class ValidatorStringTests(unittest.TestCase):
    def test_min_and_max_length(self):
        self.assertIsNone(validate("p", {"count": 5, "name": "abc"},
                                   _CLOSED))
        self.assertIsNotNone(validate("p", {"count": 5, "name": "x"},
                                      _CLOSED))
        self.assertIsNotNone(validate(
            "p", {"count": 5, "name": "abcdef"}, _CLOSED))

    def test_pattern(self):
        schema = {"type": "object", "properties": {
            "code": {"type": "string", "pattern": r"^[A-Z]{3}$"}}}
        self.assertIsNone(validate("p", {"code": "ABC"}, schema))
        self.assertIsNotNone(validate("p", {"code": "abc"}, schema))


class ValidatorNullTests(unittest.TestCase):
    def test_null_for_typed_field_rejected(self):
        schema = {"type": "object", "properties": {
            "label": {"type": "string"}}}
        self.assertIsNotNone(
            validate("p", {"label": None}, schema))

    def test_null_allowed_only_where_declared(self):
        for spec in ({"type": ["string", "null"]},
                     {"type": "null"},
                     {"const": None},
                     {"enum": ["a", None]}):
            schema = {"type": "object", "properties": {"label": spec}}
            self.assertIsNone(validate("p", {"label": None}, schema),
                              spec)
        # Required + null for a non-nullable field is a
        # rejection, not a silent absent.
        schema = {"type": "object",
                  "properties": {"label": {"type": "string"}},
                  "required": ["label"]}
        self.assertIsNotNone(
            validate("p", {"label": None}, schema))

    def test_null_passes_for_typeless_property(self):
        # Four real tools declare typeless int-or-string props;
        # a spec with no type/enum/const constrains nothing.
        schema = {"type": "object", "properties": {
            "value": {"description": "a number or a string"}}}
        self.assertIsNone(validate("p", {"value": None}, schema))
        self.assertIsNone(validate("p", {"value": 3}, schema))
        self.assertIsNone(validate("p", {"value": "x"}, schema))


class ValidatorBoolEnumTests(unittest.TestCase):
    def test_bool_is_not_integer_and_int_is_not_bool(self):
        schema = {"type": "object", "properties": {
            "n": {"type": "integer"}, "b": {"type": "boolean"}}}
        self.assertIsNotNone(validate("p", {"n": True}, schema))
        self.assertIsNotNone(validate("p", {"b": 1}, schema))
        self.assertIsNone(validate("p", {"n": 1, "b": True}, schema))

    def test_enum_strict_json_equality(self):
        schema = {"type": "object", "properties": {
            "n": {"enum": [1, 2]}}}
        # The old `value not in enum` check let True pass here
        # because True == 1 in Python.
        self.assertIsNotNone(validate("p", {"n": True}, schema))
        self.assertIsNone(validate("p", {"n": 1}, schema))
        self.assertIsNotNone(validate("p", {"n": 3}, schema))

    def test_const(self):
        schema = {"type": "object", "properties": {
            "tool": {"const": "probe"}}}
        self.assertIsNone(validate("p", {"tool": "probe"}, schema))
        self.assertIsNotNone(validate("p", {"tool": "other"}, schema))


class ValidatorArrayTests(unittest.TestCase):
    def test_array_item_types_audit_case(self):
        # The audit's merge_pdfs case: paths=['/tmp/a.pdf', 123]
        # must be rejected, not accepted.
        schema = {"type": "object", "properties": {
            "paths": {"type": "array",
                      "items": {"type": "string"}},
            "output": {"type": "string"}},
                  "required": ["paths"]}
        self.assertIsNone(validate(
            "merge_pdfs", {"paths": ["/tmp/a.pdf", "/tmp/b.pdf"]},
            schema))
        self.assertIsNotNone(validate(
            "merge_pdfs", {"paths": ["/tmp/a.pdf", 123]}, schema))

    def test_array_length_and_uniqueness(self):
        schema = {"type": "object", "properties": {
            "xs": {"type": "array", "items": {"type": "integer"},
                   "minItems": 2, "maxItems": 3,
                   "uniqueItems": True}}}
        self.assertIsNone(validate("p", {"xs": [1, 2]}, schema))
        self.assertIsNotNone(validate("p", {"xs": [1]}, schema))
        self.assertIsNotNone(validate("p", {"xs": [1, 2, 3, 4]},
                                      schema))
        self.assertIsNotNone(validate("p", {"xs": [1, 1]}, schema))

    def test_real_merge_pdfs_declaration(self):
        from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)

        self.assertIsNone(tools.validate_args(
            "merge_pdfs", {"paths": ["/tmp/a.pdf", "/tmp/b.pdf"]}))
        self.assertIsNotNone(tools.validate_args(
            "merge_pdfs", {"paths": ["/tmp/a.pdf", 123]}))


class ValidatorUnsupportedTests(unittest.TestCase):
    def test_unsupported_keyword_rejected_not_accepted(self):
        schema = {"type": "object", "properties": {
            "a": {"type": "integer", "contains": 1}}}
        problem = validate("p", {"a": 1}, schema)
        self.assertIsNotNone(problem)
        self.assertIn("unsupported", problem)

    def test_unknown_type_rejected_not_accepted(self):
        schema = {"type": "object", "properties": {
            "a": {"type": "date"}}}
        problem = validate("p", {"a": "2026-01-01"}, schema)
        self.assertIsNotNone(problem)
        self.assertIn("unsupported", problem)

    def test_type_list(self):
        schema = {"type": "object", "properties": {
            "a": {"type": ["string", "integer"]}}}
        self.assertIsNone(validate("p", {"a": "x"}, schema))
        self.assertIsNone(validate("p", {"a": 3}, schema))
        self.assertIsNotNone(validate("p", {"a": 1.5}, schema))


class ToolboxCallMalformedTests(unittest.TestCase):
    def setUp(self):
        self.reg = _FakeRegistry()

    def test_omitted_args_still_work(self):
        # None is use_toolbox's own omitted default.
        out = toolbox_mod._call(self.reg, "probe_tool", None)
        self.assertIn("ran probe_tool", out)

    def test_valid_json_string_still_parsed(self):
        out = toolbox_mod._call(self.reg, "probe_tool", '{"x": 1}')
        self.assertIn("ran probe_tool", out)
        self.assertIn("'x': 1", out)

    def test_malformed_shapes_rejected_not_dispatched(self):
        for bad in ('{"x":', '[1, 2]', '123', '"text"', 'null',
                    42, 1.5, True, ["a"], {"nested": "dict"}):
            if isinstance(bad, dict):
                continue  # a dict is a valid bundle, not malformed
            out = toolbox_mod._call(self.reg, "probe_tool", bad)
            self.assertNotIn("ran probe_tool", out, bad)
            # Outer dispatcher and inner tool kept separate,
            # and the received bundle preserved.
            self.assertIn("use_toolbox", out, bad)
            self.assertIn("probe_tool", out, bad)
            self.assertIn("did not run", out, bad)

    def test_malformed_json_string_preserves_input(self):
        out = toolbox_mod._call(self.reg, "probe_tool", '{"x":')
        self.assertIn('{"x":', out)


class DispatchRouteMalformedTests(unittest.TestCase):
    def setUp(self):
        # A test double, deliberately: these tests must not
        # consume the shared executor safety budget (a real
        # success here tipped a later budget-fragile test in
        # the full run — see the A4 report). Rejection happens
        # before execute() either way, so the double proves
        # the same dispatch behaviour.
        self.tools = _FakeRegistry()

    def test_omitted_args_keep_old_behaviour(self):
        # {} is what the Live adapter passes when the model
        # omits args: it passes through to the funnel, it is
        # NOT a malformed-bundle rejection.
        out = routers_mod.dispatch_route(
            self.tools, "route_system_apps", "feel_orb_map", {})
        self.assertEqual(out.get("status"), "success", out)
        self.assertEqual(self.tools.last_args, {})

    def test_omitted_args_reach_real_validation(self):
        # The same omitted {} on the real funnel fails
        # validation (missing required 'level') — validation
        # runs before any budget counting, so this consumes
        # no budget either.
        from core.funnel.tools_registry import tools  # Phase 4 S1a INTENTIONAL: core.tools shim -> funnel target (see refactor_phase4_deadcode_plan.md §7 S1a)

        out = routers_mod.dispatch_route(
            tools, "route_system_apps", "feel_orb_map", {})
        self.assertEqual(out.get("kind"), "validation", out)

    def test_valid_json_string_still_parsed(self):
        out = routers_mod.dispatch_route(
            self.tools, "route_system_apps", "feel_orb_map",
            '{"level": 0.5}')
        self.assertEqual(out.get("status"), "success", out)
        self.assertEqual(self.tools.last_args, {"level": 0.5})

    def test_malformed_shapes_rejected_not_dispatched(self):
        for bad in ('{"level":', '[1, 2]', '123', '"text"', 'null',
                    None, 42, ["a"]):
            self.tools.last_args = "untouched"
            out = routers_mod.dispatch_route(
                self.tools, "route_system_apps", "feel_orb_map", bad)
            # Audit A5 (P1-10): INTENTIONAL behavior change — the funnel vocabulary replaces legacy status "error" with "refused" for refusals; kind/message unchanged.
            self.assertEqual(out.get("status"), "refused", bad)
            self.assertEqual(out.get("kind"), "validation", bad)
            # Router (outer) and tool (inner) kept separate.
            self.assertEqual(out.get("router"), "route_system_apps",
                             bad)
            self.assertEqual(out.get("tool"), "feel_orb_map", bad)
            self.assertIn("route_system_apps",
                          out.get("message", ""), bad)
            self.assertIn("feel_orb_map", out.get("message", ""), bad)
            self.assertIn("malformed_args", out, bad)
            self.assertEqual(self.tools.last_args, "untouched", bad)

    def test_malformed_string_preserves_input(self):
        out = routers_mod.dispatch_route(
            self.tools, "route_system_apps", "feel_orb_map",
            '{"level":')
        self.assertIn('{"level":', out.get("message", ""), out)
        self.assertIn('{"level":', out.get("malformed_args", ""), out)


if __name__ == "__main__":
    unittest.main()
