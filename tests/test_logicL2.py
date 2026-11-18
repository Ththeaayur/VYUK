"""Logic audit L2 — schema validator hardening (LOG-03/LOG-04/LOG-05).

Covers: boolean exclusive bounds branching before numeric values;
a fail-closed schema-shape gate (malformed keywords reject as
invalid schema instead of silently weakening the schema);
required as a list of unique strings (no TypeError, no silent
duplicates); and the MCP boundary guard (malformed remote
inputSchema -> structured invalid_schema, never a dispatch,
never an exception out of execute()).

Guest rule (test-isolation batch): this suite snapshots the
process-global tool-call budget timestamps in setUpModule and
restores them byte-for-byte in tearDownModule, so the run's
failure profile is not perturbed. Validation refusals consume
no budget anyway (validation runs before budget counting);
the one valid MCP dispatch in this suite is restored likewise.
"""

import unittest

from core.funnel.tools_registry import (
    _check_value as check_value,
    _validate_args_against_schema as validate,
    _validate_schema as validate_schema,
)  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel import tools_registry as tools_registry_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/
from core.funnel import tools_registry_exec_setup as tools_registry_exec_setup_mod  # INTENTIONAL (GAPS-12): MCP validation resolves the validator through the exec-setup module's globals; patch that binding, not the facade's
from core.funnel import tools_registry_helpers as tools_registry_helpers_mod  # INTENTIONAL (batch 1): validator binding dual-patch target
from core.funnel import tools_handlers as tools_handlers_mod  # Phase 3 F3 INTENTIONAL: moved to core/funnel/

_SAVED_TIMESTAMPS = []


def setUpModule():
    global _SAVED_TIMESTAMPS
    with tools_handlers_mod._budget_lock:
        _SAVED_TIMESTAMPS = list(
            tools_handlers_mod._budget_timestamps)


def tearDownModule():
    with tools_handlers_mod._budget_lock:
        tools_handlers_mod._budget_timestamps[:] = _SAVED_TIMESTAMPS


def _obj(props, **kw):
    """Build a root object schema with the given properties."""
    schema = {"type": "object", "properties": props}
    schema.update(kw)
    return schema


class TestExclusiveBounds(unittest.TestCase):
    """LOG-03: booleans branch before numbers."""

    def test_legacy_true_minimum(self):
        spec = {"type": "number", "minimum": 0, "exclusiveMinimum": True}
        self.assertIsNone(check_value("x", "x", 0.5, spec))
        self.assertIsNotNone(check_value("x", "x", 0, spec))
        self.assertIn("greater than 0",
                      check_value("x", "x", 0, spec))
        # Below the inclusive minimum still fails on the minimum.
        self.assertIsNotNone(check_value("x", "x", -1, spec))

    def test_legacy_true_maximum(self):
        spec = {"type": "number", "maximum": 10, "exclusiveMaximum": True}
        self.assertIsNone(check_value("x", "x", 9.5, spec))
        self.assertIsNotNone(check_value("x", "x", 10, spec))
        self.assertIn("less than 10",
                      check_value("x", "x", 10, spec))
        self.assertIsNotNone(check_value("x", "x", 11, spec))

    def test_legacy_false_imposes_nothing(self):
        spec = {"type": "number", "minimum": 0, "exclusiveMinimum": False}
        self.assertIsNone(check_value("x", "x", 0, spec))
        spec = {"type": "number", "maximum": 10, "exclusiveMaximum": False}
        self.assertIsNone(check_value("x", "x", 10, spec))

    def test_legacy_true_without_minimum_imposes_nothing(self):
        # Draft-4 exclusiveMinimum modifies minimum; with no
        # minimum there is nothing to make exclusive (the old
        # code wrongly imposed the number 1 here).
        spec = {"type": "number", "exclusiveMinimum": True}
        self.assertIsNone(check_value("x", "x", 0.5, spec))
        self.assertIsNone(check_value("x", "x", -100, spec))

    def test_numeric_exclusive_bounds(self):
        spec = {"type": "number", "exclusiveMinimum": 5}
        self.assertIsNotNone(check_value("x", "x", 5, spec))
        self.assertIsNone(check_value("x", "x", 6, spec))
        spec = {"type": "number", "exclusiveMaximum": 5}
        self.assertIsNotNone(check_value("x", "x", 5, spec))
        self.assertIsNone(check_value("x", "x", 4, spec))

    def test_bool_bounds_are_invalid_schema_via_funnel(self):
        for bad in ({"type": "number", "minimum": True},
                    {"type": "number", "maximum": False},
                    {"type": "number", "minimum": 0,
                     "exclusiveMinimum": "5"},
                    {"type": "number", "multipleOf": True}):
            out = validate("x", {"v": 1},
                           _obj({"v": bad}))
            self.assertIsNotNone(out, bad)
            self.assertIn("invalid schema", out, bad)

    def test_bool_length_and_count_never_act_as_numbers(self):
        # Direct _check_value: a bool length/count must not act
        # as 1/0 (via the funnel the schema gate rejects it).
        spec = {"type": "string", "minLength": True}
        self.assertIsNone(check_value("x", "x", "", spec))
        spec = {"type": "array", "maxItems": False}
        self.assertIsNone(check_value("x", "x", [1, 2, 3], spec))


class TestSchemaShape(unittest.TestCase):
    """LOG-04: malformed keywords fail closed, never weaken."""

    def assertInvalidSchema(self, schema, args):
        out = validate("x", args, schema)
        self.assertIsNotNone(out, schema)
        self.assertIn("invalid schema", out, schema)
        return out

    def test_empty_and_non_list_enum(self):
        for bad in ([], "red", 5, {"a": 1}):
            self.assertInvalidSchema(
                _obj({"v": {"enum": bad}}), {"v": "anything"})

    def test_valid_enum_still_enforces(self):
        schema = _obj({"v": {"enum": ["red", "blue"]}})
        self.assertIsNone(validate("x", {"v": "red"}, schema))
        self.assertIsNotNone(validate("x", {"v": "green"}, schema))

    def test_non_numeric_and_non_finite_bounds(self):
        for bad in ({"type": "number", "minimum": "3"},
                    {"type": "number", "maximum": None},
                    {"type": "number", "minimum": float("nan")},
                    {"type": "number", "maximum": float("inf")}):
            self.assertInvalidSchema(_obj({"v": bad}), {"v": 3})

    def test_multiple_of_must_be_positive_number(self):
        for bad in (0, -2, True, "2"):
            self.assertInvalidSchema(
                _obj({"v": {"type": "number", "multipleOf": bad}}),
                {"v": 4})
        schema = _obj({"v": {"type": "number", "multipleOf": 2}})
        self.assertIsNone(validate("x", {"v": 4}, schema))
        self.assertIsNotNone(validate("x", {"v": 3}, schema))

    def test_additional_properties_shape(self):
        for bad in ("yes", 5, None, ["x"]):
            self.assertInvalidSchema(
                _obj({"a": {"type": "integer"}},
                     additionalProperties=bad),
                {"a": 1, "zz": 2})
        # The valid shapes keep their A4 behaviour.
        schema = _obj({"a": {"type": "integer"}},
                      additionalProperties=False)
        self.assertIsNotNone(validate("x", {"a": 1, "zz": 2}, schema))
        schema = _obj({"a": {"type": "integer"}})
        self.assertIsNone(validate("x", {"a": 1, "zz": 2}, schema))
        schema = _obj({"a": {"type": "integer"}},
                      additionalProperties=True)
        self.assertIsNone(validate("x", {"a": 1, "zz": 2}, schema))

    def test_length_and_count_shapes(self):
        for kw, bad in (("minLength", -1), ("maxLength", True),
                        ("minItems", 2.5), ("maxItems", "3"),
                        ("minProperties", -1), ("maxProperties", False)):
            prop = {"type": "string", kw: bad} \
                if "Length" in kw else \
                {"type": "array", kw: bad} \
                if "Items" in kw else \
                {"type": "object", kw: bad}
            arg = {"v": "ab"} if "Length" in kw else \
                {"v": [1, 2]} if "Items" in kw else {"v": {}}
            # minProperties/maxProperties live on the object
            # itself, not on a property.
            if "Properties" in kw:
                schema = {"type": "object", kw: bad}
                self.assertInvalidSchema(schema, {})
            else:
                self.assertInvalidSchema(_obj({"v": prop}), arg)

    def test_items_and_additional_items_shapes(self):
        self.assertInvalidSchema(
            _obj({"v": {"type": "array", "items": "nope"}}),
            {"v": [1]})
        self.assertInvalidSchema(
            _obj({"v": {"type": "array", "items": [{"type": "integer"}],
                        "additionalItems": "nope"}}),
            {"v": [1, 2]})
        # Valid shapes keep working.
        schema = _obj({"v": {"type": "array",
                             "items": {"type": "integer"}}})
        self.assertIsNone(validate("x", {"v": [1, 2]}, schema))
        self.assertIsNotNone(validate("x", {"v": ["a"]}, schema))
        schema = _obj({"v": {"type": "array",
                             "items": [{"type": "integer"}],
                             "additionalItems": False}})
        self.assertIsNotNone(validate("x", {"v": [1, 2]}, schema))

    def test_unique_items_and_pattern_shapes(self):
        self.assertInvalidSchema(
            _obj({"v": {"type": "array", "uniqueItems": "yes"}}),
            {"v": [1]})
        self.assertInvalidSchema(
            _obj({"v": {"type": "string", "pattern": 5}}),
            {"v": "ab"})
        self.assertInvalidSchema(
            _obj({"v": {"type": "string", "pattern": "([a-"}}),
            {"v": "ab"})
        schema = _obj({"v": {"type": "array", "uniqueItems": True}})
        self.assertIsNotNone(validate("x", {"v": [1, 1]}, schema))
        self.assertIsNone(validate("x", {"v": [1, 2]}, schema))

    def test_type_and_composition_shapes(self):
        self.assertInvalidSchema(
            {"type": 5, "properties": {}}, {})
        self.assertInvalidSchema(
            {"type": ["string", 7], "properties": {}}, {})
        self.assertInvalidSchema(
            {"type": "date", "properties": {}}, {})
        self.assertInvalidSchema(
            {"allOf": [], "type": "object"}, {})
        self.assertInvalidSchema(
            {"not": "nope", "type": "object"}, {})

    def test_properties_and_nested_shapes(self):
        self.assertInvalidSchema(
            {"type": "object", "properties": ["a"]}, {})
        # A malformed nested node is caught even when the value
        # never exercises it — the whole point of the tree gate.
        schema = _obj({"a": {"enum": []}})
        self.assertInvalidSchema(schema, {})
        schema = _obj({"a": {"type": "object",
                             "properties": {"b": {"type": "number",
                                                  "minimum": "x"}}}})
        self.assertInvalidSchema(schema, {"a": {}})

    def test_deep_nesting_is_invalid_not_a_crash(self):
        deep = {"type": "object"}
        node = deep
        for i in range(150):
            nxt = {"type": "object", "properties": {}}
            node["properties"] = {"n": nxt}
            node = nxt
        out = validate("x", {}, deep)
        self.assertIsNotNone(out)
        self.assertIn("invalid schema", out)
        # A modestly nested valid schema still passes.
        shallow = {"type": "object",
                   "properties": {"a": {"type": "object",
                                        "properties": {
                                            "b": {"type": "integer"}}}}}
        self.assertIsNone(validate("x", {"a": {"b": 1}}, shallow))


class TestRequired(unittest.TestCase):
    """LOG-05: required is a list of unique strings, or invalid."""

    def test_non_string_members_never_raise(self):
        for bad in ([["x"]], [1], [None], [{"a": 1}], ["a", 2]):
            out = validate("x", {}, {"type": "object",
                                     "required": bad})
            self.assertIsNotNone(out, bad)
            self.assertIn("invalid schema", out, bad)
        # Direct _check_value: same, and crucially no raise.
        out = check_value("x", "", {},
                          {"type": "object", "required": [["x"]]})
        self.assertIsNotNone(out)
        self.assertIn("invalid schema", out)

    def test_duplicate_and_non_list_required(self):
        for bad in (["a", "a"], "a", None, 5):
            out = validate("x", {"a": 1},
                           {"type": "object", "required": bad})
            self.assertIsNotNone(out, bad)
            self.assertIn("invalid schema", out, bad)

    def test_valid_required_keeps_argument_semantics(self):
        schema = {"type": "object", "required": ["a"]}
        out = validate("x", {}, schema)
        self.assertIsNotNone(out)
        self.assertIn("is required but was not provided", out)
        self.assertNotIn("invalid schema", out)
        self.assertIsNone(validate("x", {"a": 1}, schema))

    def test_nested_malformed_required_caught_without_value(self):
        schema = _obj({"o": {"type": "object", "required": [1]}})
        out = validate("x", {}, schema)
        self.assertIsNotNone(out)
        self.assertIn("invalid schema", out)


class TestA4SemanticsHold(unittest.TestCase):
    """A4 contract spots the L2 change must not weaken."""

    def test_strict_json_equality(self):
        # True never satisfies an integer enum; 1 never
        # satisfies a boolean type.
        schema = _obj({"v": {"enum": [1]}})
        self.assertIsNotNone(validate("x", {"v": True}, schema))
        schema = _obj({"v": {"type": "boolean"}})
        self.assertIsNotNone(validate("x", {"v": 1}, schema))
        self.assertIsNone(validate("x", {"v": True}, schema))

    def test_additional_properties_absent_still_allowed(self):
        schema = _obj({"a": {"type": "integer"}})
        self.assertIsNone(validate("x", {"a": 1, "zz": 2}, schema))

    def test_null_rules_unchanged(self):
        schema = _obj({"v": {"type": "string"}})
        self.assertIsNotNone(validate("x", {"v": None}, schema))
        schema = _obj({"v": {}})
        self.assertIsNone(validate("x", {"v": None}, schema))
        schema = _obj({"v": {"type": ["string", "null"]}})
        self.assertIsNone(validate("x", {"v": None}, schema))

    def test_omitted_vs_malformed_args(self):
        schema = _obj({"a": {"type": "integer"}}, required=["a"])
        out = validate("x", {}, schema)
        self.assertIsNotNone(out)
        self.assertIn("is required but was not provided", out)
        out = validate("x", ["not", "a", "dict"], schema)
        self.assertIsNotNone(out)
        self.assertIn("arguments must be an object", out)

    def test_typeless_props_still_unconstrained(self):
        # The roman_convert/base_convert/epoch_convert shape:
        # a property with no type/enum/const constrains nothing.
        schema = _obj({"v": {}})
        self.assertIsNone(validate("x", {"v": "text"}, schema))
        self.assertIsNone(validate("x", {"v": 7}, schema))
        self.assertIsNone(validate("x", {"v": None}, schema))


class _FakeBus:
    """Minimal MCP bus (A2/A3 pattern) with dispatch recording."""

    def __init__(self, entries):
        self._entries = {e["name"]: e for e in entries}
        self.dispatched = []

    def resolve(self, name):
        return self._entries.get(name)

    def tool_changed(self, name):
        return False

    def ensure_server(self, server):
        return True, None

    def call_tool_sync(self, server, tool, args, timeout=120.0):
        self.dispatched.append((server, tool))
        return True, "fake mcp result", None


class TestMCPBoundary(unittest.TestCase):
    """MCP boundary: malformed schema -> invalid_schema, no dispatch."""

    def setUp(self):
        import core.mcp_client as mcp_client

        self._mcp_client = mcp_client
        self._orig_get_bus = mcp_client.get_bus

    def tearDown(self):
        self._mcp_client.get_bus = self._orig_get_bus

    def _run(self, parameters, args):
        bus = _FakeBus([])
        bus._entries = {"mcp__l2srv__probe":
                        {"server": "l2srv", "tool": "probe",
                         "annotations": {}, "parameters": parameters,
                         "description": "L2 probe"}}
        self._mcp_client.get_bus = lambda: bus
        reg = tools_registry_mod.ToolRegistry()
        result = reg.execute("mcp__l2srv__probe", args)
        return result, bus.dispatched

    def test_malformed_schema_is_invalid_schema_no_dispatch(self):
        for bad in ({"type": "object", "required": [["x"]]},
                    {"type": "object",
                     "properties": {"v": {"enum": []}}},
                    {"type": "object",
                     "properties": {"v": {"type": "number",
                                          "minimum": "3"}}}):
            result, dispatched = self._run(bad, {"v": 1})
            self.assertEqual(result.get("kind"), "invalid_schema",
                             bad)
            self.assertEqual(result.get("status"), "refused", bad)
            self.assertTrue(result.get("validation_error"), bad)
            self.assertIn("invalid schema",
                          result.get("message", ""), bad)
            self.assertEqual(dispatched, [], bad)

    def test_argument_problem_stays_validation(self):
        params = {"type": "object",
                  "properties": {"v": {"type": "integer"}},
                  "required": ["v"]}
        result, dispatched = self._run(params, {"v": "three"})
        self.assertEqual(result.get("kind"), "validation", result)
        self.assertEqual(dispatched, [])

    def test_guard_converts_unexpected_exception(self):
        orig = tools_registry_exec_setup_mod._validate_args_against_schema
        # INTENTIONAL (GAPS-12): the MCP validation branch lives in
        # _exec_validate (tools_registry_exec_setup) and resolves the
        # validator through that module's globals — patch the binding
        # the unified code actually resolves through, and restore it.
        # (The batch-1 dual-patch target is gone with _execute_mcp;
        # patching the facade/helpers bindings would prove nothing.)
        fake = lambda *a, **k: (_ for _ in ()).throw(  # noqa: E731
            RuntimeError("boom"))
        tools_registry_exec_setup_mod._validate_args_against_schema = fake
        try:
            result, dispatched = self._run(
                {"type": "object", "properties": {}}, {})
        finally:
            tools_registry_exec_setup_mod._validate_args_against_schema = orig
        self.assertEqual(result.get("kind"), "invalid_schema", result)
        self.assertEqual(result.get("status"), "refused", result)
        self.assertEqual(dispatched, [])

    def test_valid_schema_still_dispatches(self):
        import core.mcp_policy as mcp_policy

        params = {"type": "object", "properties": {}}
        entry = {"server": "l2srv", "tool": "probe",
                 "annotations": {},
                 "parameters": params, "description": "L2 probe"}
        mcp_policy.set_store({
            mcp_policy.policy_key(entry): {
                "class": "read",
                "digest": mcp_policy.declaration_digest(entry)}})
        try:
            bus = _FakeBus([])
            bus._entries = {"mcp__l2srv__probe": entry}
            self._mcp_client.get_bus = lambda: bus
            reg = tools_registry_mod.ToolRegistry()
            result = reg.execute("mcp__l2srv__probe", {})
        finally:
            mcp_policy.reset_store()
        self.assertEqual(result.get("status"), "success", result)
        self.assertEqual(bus.dispatched, [("l2srv", "probe")])


if __name__ == "__main__":
    unittest.main()
